#include "fem/deformable.h"
#include "common/common.h"
#include "solver/matrix_op.h"
#include "Eigen/SparseCholesky"

template<int vertex_dim, int element_dim>
void Deformable<vertex_dim, element_dim>::BackwardNewton(const std::string& method, const VectorXr& q, const VectorXr& v,
    const VectorXr& a, const VectorXr& f_ext, const real dt, const VectorXr& q_next, const VectorXr& v_next, const VectorXr& dl_dq_next,
    const VectorXr& dl_dv_next, const std::map<std::string, real>& options,
    VectorXr& dl_dq, VectorXr& dl_dv, VectorXr& dl_da, VectorXr& dl_df_ext, VectorXr& dl_dw) const {
    CheckError(method == "newton_pcg" || method == "newton_cholesky", "Unsupported Newton's method: " + method);
    CheckError(options.find("abs_tol") != options.end(), "Missing option abs_tol.");
    CheckError(options.find("rel_tol") != options.end(), "Missing option rel_tol.");
    CheckError(options.find("thread_ct") != options.end(), "Missing option thread_ct.");
    const real abs_tol = options.at("abs_tol");
    const real rel_tol = options.at("rel_tol");
    const int thread_ct = static_cast<int>(options.at("thread_ct"));
    for (const auto& pair : dirichlet_) CheckError(q_next(pair.first) == pair.second, "Inconsistent q_next.");

    omp_set_num_threads(thread_ct);
    dl_dq = VectorXr::Zero(dofs_);
    dl_dv = VectorXr::Zero(dofs_);
    dl_da = VectorXr::Zero(act_dofs_);
    dl_df_ext = VectorXr::Zero(dofs_);
    const int w_dofs = static_cast<int>(pd_element_energies_.size());
    dl_dw = VectorXr::Zero(w_dofs);

    // Step 6: compute v_next: q, q_next -> v_next.
    // v_next = (q_next - q) / dt.
    const real mass = density_ * cell_volume_;
    const real h = dt;
    const real hm = dt / mass;
    const real h2m = hm * dt;
    const real inv_dt = 1 / dt;
    VectorXr dl_dq_next_agg = dl_dq_next;
    dl_dq_next_agg += dl_dv_next * inv_dt;
    dl_dq += -dl_dv_next * inv_dt;

    // Step 5: compute q_next: a, rhs_friction -> q_next.
    // q_next - h2m * (f_ela(q_next) + f_pd(q_next) + f_act(q_next, a)) = rhs_friction.
    // and certain q_next DoFs are directly copied from rhs_friction.
    // Let n be the dim of q_next. Let m be the dim of frozen DoFs.
    // lhs(q_next_free; rhs_friction_fixed; a) = rhs_friction_free.
    // lhs: R^(n - m) x R^m -> R^(n - m).
    // dlhs/dq_next_free * dq_next_free + dlhs/drhs_friction_fixed * drhs_friction_fixed
    // + dlhs/da * da = drhs_friction_free.
    // q_next_fixed = rhs_friction_fixed.
    const VectorXr forward_state_force = ForwardStateForce(q, v);
    const VectorXr v_pred = v + hm * (f_ext + ElasticForce(q) + forward_state_force
        + PdEnergyForce(q) + ActuationForce(q, a));

    const VectorXr rhs = q + h * v + h2m * f_ext + h2m * forward_state_force;
    VectorXr rhs_dirichlet = rhs;
    for (const auto& pair : dirichlet_) rhs_dirichlet(pair.first) = pair.second;

    VectorXr rhs_friction = rhs_dirichlet;
    std::map<int, real> dirichlet_with_friction = dirichlet_;
    for (const auto& pair : frictional_boundary_vertex_indices_) {
        const int idx = pair.first;
        const Eigen::Matrix<real, vertex_dim, 1> qi = q.segment(vertex_dim * idx, vertex_dim);
        const Eigen::Matrix<real, vertex_dim, 1> vi = v_pred.segment(vertex_dim * idx, vertex_dim);
        real t_hit;
        if (frictional_boundary_->ForwardIntersect(qi, vi, dt, t_hit)) {
            const Eigen::Matrix<real, vertex_dim, 1> qi_hit = qi + t_hit * vi;
            for (int i = 0; i < vertex_dim; ++i) {
                rhs_friction(vertex_dim * idx + i) = qi_hit(i);
                dirichlet_with_friction[vertex_dim * idx + i] = qi_hit(i);
            }
        }
    }
    // Backpropagate rhs_friction -> q_next.
    VectorXr adjoint = VectorXr::Zero(dofs_);
    if (method == "newton_pcg") {
        Eigen::ConjugateGradient<SparseMatrix, Eigen::Lower|Eigen::Upper> cg;
        // Setting up cg termination conditions: here what you set is the upper bound of:
        // |Ax - b|/|b| <= tolerance.
        // In our implementation of the projective dynamics, we use the termination condition:
        // |Ax - b| <= rel_tol * |b| + abs_tol.
        // or equivalently,
        // |Ax - b|/|b| <= rel_tol + abs_tol/|b|.
        const real tol = rel_tol + abs_tol / dl_dq_next_agg.norm();
        cg.setTolerance(tol);
        const SparseMatrix op = NewtonMatrix(q_next, a, h2m, dirichlet_with_friction);
        cg.compute(op);
        adjoint = cg.solve(dl_dq_next_agg);
        CheckError(cg.info() == Eigen::Success, "CG solver failed.");
    } else if (method == "newton_cholesky") {
        // Note that Cholesky is a direct solver: no tolerance is ever used to terminate the solution.
        Eigen::SimplicialLDLT<SparseMatrix> cholesky;
        const SparseMatrix op = NewtonMatrix(q_next, a, h2m, dirichlet_with_friction);
        cholesky.compute(op);
        adjoint = cholesky.solve(dl_dq_next_agg);
        CheckError(cholesky.info() == Eigen::Success, "Cholesky solver failed.");
    } else {
        // Should never happen.
    }
    // dlhs/dq_next_free * dq_next_free = drhs_friction_free - dlhs/drhs_friction_fixed * drhs_friction_fixed.
    // dq_next_free = J^{-1} * drhs_friction_free - J^{-1} * (dlhs/drhs_friction_fixed) * drhs_friction_fixed.
    // q_next_fixed = rhs_friction_fixed.
    VectorXr adjoint_with_zero = adjoint;
    for (const auto& pair : dirichlet_with_friction) adjoint_with_zero(pair.first) = 0;
    // Additionally, need to add -adjoint_with_zero * (dlhs/drhs_friction_fixed) to rows corresponding to fixed DoFs.
    // TODO: this could be made faster.
    VectorXr dl_drhs_friction_fixed = NewtonMatrixOp(q_next, a, h2m, {}, -adjoint_with_zero);
    VectorXr dl_drhs_friction = adjoint;
    for (const auto& pair : dirichlet_with_friction)
        dl_drhs_friction(pair.first) += dl_drhs_friction_fixed(pair.first);

    // Backpropagate a -> q_next.
    // dlhs/dq_next_free * dq_next_free + dlhs/da * da = 0.
    SparseMatrixElements nonzeros_q, nonzeros_a;
    ActuationForceDifferential(q_next, a, nonzeros_q, nonzeros_a);
    dl_da += VectorXr(adjoint_with_zero.transpose() * ToSparseMatrix(dofs_, act_dofs_, nonzeros_a) * h2m);

    // Backpropagate w -> q_next.
    SparseMatrixElements nonzeros_w;
    PdEnergyForceDifferential(q_next, false, true, nonzeros_q, nonzeros_w);
    dl_dw += VectorXr(adjoint_with_zero.transpose() * ToSparseMatrix(dofs_, w_dofs, nonzeros_w) * h2m);

    // Step 4: q, v_pred, rhs_dirichlet -> rhs_friction.
    VectorXr dl_drhs_dirichlet = dl_drhs_friction;
    VectorXr dl_dv_pred = VectorXr::Zero(dofs_);
    for (const auto& pair : frictional_boundary_vertex_indices_) {
        const int idx = pair.first;
        const Eigen::Matrix<real, vertex_dim, 1> qi = q.segment(vertex_dim * idx, vertex_dim);
        const Eigen::Matrix<real, vertex_dim, 1> vi_pred = v_pred.segment(vertex_dim * idx, vertex_dim);
        real t_hit;
        if (frictional_boundary_->ForwardIntersect(qi, vi_pred, dt, t_hit)) {
            dl_drhs_dirichlet.segment(vertex_dim * idx, vertex_dim) = Eigen::Matrix<real, vertex_dim, 1>::Zero();
            Eigen::Matrix<real, vertex_dim, 1> dl_dqi, dl_dvi_pred;
            frictional_boundary_->BackwardIntersect(qi, vi_pred, t_hit,
                dl_drhs_friction.segment(vertex_dim * idx, vertex_dim), dl_dqi, dl_dvi_pred);
            dl_dq.segment(vertex_dim * idx, vertex_dim) += dl_dqi;
            dl_dv_pred.segment(vertex_dim * idx, vertex_dim) += dl_dvi_pred;
        }
    }

    // Step 3: merge dirichlet: rhs -> rhs_dirichlet.
    // rhs_dirichlet = rhs \/ dirichlet_.
    VectorXr dl_drhs = dl_drhs_dirichlet;
    for (const auto& pair : dirichlet_) dl_drhs(pair.first) = 0;

    // Step 2: compute rhs: q, v, f_ext -> rhs.
    // rhs = q + h * v + h2m * f_ext + h2m * f_state(q, v).
    dl_dq += dl_drhs;
    dl_dv += dl_drhs * h;
    dl_df_ext += dl_drhs * h2m;
    VectorXr dl_dq_single, dl_dv_single;
    BackwardStateForce(q, v, forward_state_force, dl_drhs * h2m, dl_dq_single, dl_dv_single);
    dl_dq += dl_dq_single;
    dl_dv += dl_dv_single;

    // Step 1: compute predicted velocity: q, v, a, f_ext -> v_pred.
    // v_pred = v + h / m * (f_ext + f_ela(q) + f_state(q, v) + f_pd(q) + f_act(q, a)).
    dl_dv += dl_dv_pred;
    dl_df_ext += dl_dv_pred * hm;
    dl_da += ElasticForceDifferential(q, dl_dv_pred) * hm;
    BackwardStateForce(q, v, forward_state_force, dl_dv_pred * hm, dl_dq_single, dl_dv_single);
    dl_dq += dl_dq_single;
    dl_dv += dl_dv_single;
    PdEnergyForceDifferential(q, false, true, nonzeros_q, nonzeros_w);
    dl_dq += PdEnergyForceDifferential(q, dl_dv_pred * hm, VectorXr::Zero(w_dofs));
    dl_dw += VectorXr(dl_dv_pred.transpose() * ToSparseMatrix(dofs_, w_dofs, nonzeros_w) * hm);
    ActuationForceDifferential(q, a, nonzeros_q, nonzeros_a);
    dl_dq += dl_dv_pred.transpose() * ToSparseMatrix(dofs_, dofs_, nonzeros_q) * hm;
    dl_da += dl_dv_pred.transpose() * ToSparseMatrix(dofs_, act_dofs_, nonzeros_a) * hm;
}

template class Deformable<2, 4>;
template class Deformable<3, 8>;