import numpy as np
from pathlib import Path
import time
import scipy.optimize
from py_diff_pd.core.py_diff_pd_core import Mesh3d, Deformable3d, StdRealVector
from py_diff_pd.common.common import create_folder, ndarray, print_info
from py_diff_pd.common.common import to_std_map, to_std_real_vector
from py_diff_pd.common.mesh import generate_hex_mesh
from py_diff_pd.common.display import display_hex_mesh, render_hex_mesh, export_gif

if __name__ == '__main__':
    seed = 42
    np.random.seed(seed)
    print_info('Seed: {}'.format(seed))

    folder = Path('open_loop_demo_3d')
    check_grad = False
    display_method = 'pbrt' # 'matplotlib' or 'pbrt'.
    render_samples = 4
    create_folder(folder)

    # Mesh parameters.
    cell_nums = (2, 2, 4)
    node_nums = (cell_nums[0] + 1, cell_nums[1] + 1, cell_nums[2] + 1)
    dx = 0.1
    origin = (0, 0, 0)
    bin_file_name = str(folder / 'cube.bin')
    voxels = np.ones(cell_nums)
    generate_hex_mesh(voxels, dx, origin, bin_file_name)
    mesh = Mesh3d()
    mesh.Initialize(bin_file_name)
    vertex_num = mesh.NumOfVertices()

    # FEM parameters.
    youngs_modulus = 1e6
    poissons_ratio = 0.45
    density = 1e3
    method = 'newton_pcg'
    opt = { 'max_newton_iter': 10, 'max_ls_iter': 10, 'rel_tol': 1e-2, 'verbose': 0 }
    deformable = Deformable3d()
    deformable.Initialize(bin_file_name, density, 'corotated', youngs_modulus, poissons_ratio)
    # Boundary conditions.
    for i in range(node_nums[0]):
        for j in range(node_nums[1]):
            node_idx = i * node_nums[1] * node_nums[2] + j * node_nums[2]
            vx, vy, vz = mesh.py_vertex(node_idx)
            deformable.SetDirichletBoundaryCondition(3 * node_idx, vx)
            deformable.SetDirichletBoundaryCondition(3 * node_idx + 1, vy)
            deformable.SetDirichletBoundaryCondition(3 * node_idx + 2, vz)

    # Forward simulation.
    dt = 1e-2
    frame_num = 25
    dofs = deformable.dofs()
    q0 = ndarray(mesh.py_vertices())
    v0 = np.zeros(dofs)
    def visualize_results(f, f_folder):
        create_folder(folder / f_folder)
        q = [q0,]
        v = [v0,]
        for i in range(frame_num):
            q_cur = q[-1]
            deformable.PySaveToMeshFile(to_std_real_vector(q_cur), str(folder / f_folder / '{:04d}.bin'.format(i)))

            v_cur = v[-1]
            q_next_array = StdRealVector(dofs)
            v_next_array = StdRealVector(dofs)
            deformable.PyForward(method, to_std_real_vector(q_cur), to_std_real_vector(v_cur),
                to_std_real_vector(f), dt, to_std_map(opt), q_next_array, v_next_array)

            q_next = ndarray(q_next_array)
            v_next = ndarray(v_next_array)
            q.append(q_next)
            v.append(v_next)

        # Display the results.
        frame_cnt = 0
        frame_skip = 1
        for i in range(0, frame_num, frame_skip):
            mesh = Mesh3d()
            mesh.Initialize(str(folder / f_folder / '{:04d}.bin'.format(frame_cnt)))
            if display_method == 'pbrt':
                render_hex_mesh(mesh, file_name=folder / f_folder / '{:04d}.png'.format(i), sample=render_samples,
                    transforms=[('t', (.1, .1, 0)), ('s', 2.5)])
            elif display_method == 'matplotlib':
                display_hex_mesh(mesh, xlim=[-dx, (cell_nums[0] + 1) * dx], ylim=[-dx, (cell_nums[1] + 1) * dx],
                    title='Frame {:04d}'.format(i), file_name=folder / f_folder / '{:04d}.png'.format(i), show=False)
            frame_cnt += 1

        export_gif(folder / f_folder, '{}.gif'.format(str(folder / f_folder)), 10)

    # Optimization.
    target_position = ndarray([(cell_nums[0] + 0.5) * dx, (cell_nums[1] + 0.5) * dx, (cell_nums[2] - 0.5) * dx])
    def loss_and_grad(f, verbose):
        t_begin = time.time()
        q = [to_std_real_vector(q0),]
        v = [to_std_real_vector(v0),]
        f_array = to_std_real_vector(f)
        for i in range(frame_num):
            q_cur = q[-1]
            v_cur = v[-1]
            q_next_array = StdRealVector(dofs)
            v_next_array = StdRealVector(dofs)
            deformable.PyForward(method, q_cur, v_cur, f_array, dt, to_std_map(opt), q_next_array, v_next_array)
            q.append(q_next_array)
            v.append(v_next_array)
        # Compute the loss.
        target_q = ndarray(q[-1])[-3:]
        loss = np.sum((target_q - target_position) ** 2)

        # Compute the gradients.
        grad = np.zeros(f.size)
        dl_dq_next = np.zeros(dofs)
        dl_dq_next[-3:] = 2 * (target_q - target_position)
        dl_dv_next = np.zeros(f.size)
        dl_dq_next = to_std_real_vector(dl_dq_next)
        dl_dv_next = to_std_real_vector(dl_dv_next)

        for i in reversed(range(frame_num)):
            dl_dq = StdRealVector(dofs)
            dl_dv = StdRealVector(dofs)
            dl_df_ext = StdRealVector(dofs)
            deformable.PyBackward(method, q[i], v[i], f_array, dt, q[i + 1], v[i + 1],
                dl_dq_next, dl_dv_next, to_std_map(opt), dl_dq, dl_dv, dl_df_ext)
            grad += ndarray(dl_df_ext)
            dl_dq_next = dl_dq
            dl_dv_next = dl_dv
        t_end = time.time()
        if verbose:
            print('loss: {:3.3e}, grad: {:3.3e}, |x|: {:3.3e}, time: {:3.3e}s'.format(loss, np.linalg.norm(grad),
                np.linalg.norm(f), t_end - t_begin))
        return loss, grad

    # Now check the gradients.
    x0 = np.random.normal(size=dofs) * density * dx * dx * dx
    if check_grad:
        from py_diff_pd.common.grad_check import check_gradients
        eps = 1e-5
        atol = 1e-4
        rtol = 1e-2
        print_info('Checking gradients...')
        check_gradients(lambda x: loss_and_grad(x, False), x0, eps, atol, rtol, True)

    # Optimize for the control signal.
    print_info('Optimizing control signals...')
    result = scipy.optimize.minimize(lambda x: loss_and_grad(x, True), np.copy(x0), method='L-BFGS-B', jac=True, bounds=None)
    assert result.success
    x_final = result.x

    # Display initial and final results.
    visualize_results(x0, 'init')
    visualize_results(x_final, 'final')