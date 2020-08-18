import sys
sys.path.append('../')

from pathlib import Path
import time
import numpy as np
import scipy.optimize
import pickle

from py_diff_pd.common.common import ndarray, create_folder, rpy_to_rotation, rpy_to_rotation_gradient
from py_diff_pd.common.common import print_info, print_ok, print_error
from py_diff_pd.common.grad_check import check_gradients
from py_diff_pd.core.py_diff_pd_core import StdRealVector
from py_diff_pd.env.cow_env_3d import CowEnv3d

if __name__ == '__main__':
    seed = 48
    folder = Path('cow_3d')
    refinement = 8
    act_max = 2.0
    youngs_modulus = 1e6
    poissons_ratio = 0.49
    target_com = ndarray([0.5, 0.5, 0.2])
    env = CowEnv3d(seed, folder, { 'refinement': refinement,
        'youngs_modulus': youngs_modulus,
        'poissons_ratio': poissons_ratio,
        'gait': 'gallop' })
    deformable = env.deformable()
    leg_indices = env._leg_indices
    spine_indices = env._spine_indices

    # Optimization parameters.
    thread_ct = 4
    newton_opt = { 'max_newton_iter': 500, 'max_ls_iter': 20, 'abs_tol': 1e-9, 'rel_tol': 1e-4, 'verbose': 0, 'thread_ct': thread_ct }
    pd_opt = { 'max_pd_iter': 500, 'max_ls_iter': 1, 'abs_tol': 1e-9, 'rel_tol': 1e-4, 'verbose': 0, 'thread_ct': thread_ct,
        'use_bfgs': 1, 'bfgs_history_size': 10 }
    methods = ('newton_pcg', 'newton_cholesky', 'pd_eigen')
    opts = (newton_opt, newton_opt, pd_opt)

    dt = 1e-3
    frame_num = 800

    # Initial state.
    dofs = deformable.dofs()
    act_dofs = deformable.act_dofs()
    q0 = env.default_init_position()
    v0 = np.zeros(dofs)
    a0 = [np.ones(act_dofs) for _ in range(frame_num)]
    f0 = [np.zeros(dofs) for _ in range(frame_num)]


    # Optimization.
    # Variables to be optimized: A_Front, A_Rear, A_Spine, w_legs, w_spine, phi_front, phi_spine
    x_lb = np.zeros(6)
    x_ub = ndarray([1, 1, 1, 2*np.pi / 100, np.pi, np.pi])
    x_init = np.random.uniform(low=0, high=1, size=6)
    x_init[3:4] *= 2 * np.pi / 100
    x_init[4:6] *= np.pi
    # Visualize initial guess.
    def variable_to_states(x, return_jac):
        A_f = x[0]
        A_b = x[1]
        A_s = x[2]
        w_l = x[3]
        # phi_f = x[4]
        phi_s = x[4]
        phi_b = x[5]
        jac = [np.zeros((6, act_dofs)) for _ in range(frame_num)]
        a = [np.zeros(act_dofs) for _ in range(frame_num)]

        for i in range(frame_num):
            for key, indcs in leg_indices.items():
                if key[-1] == 'F':
                    for idx in indcs:
                        if key[0] == 'F':
                            a[i][idx] = act_max * (1 + A_f*np.sin(w_l*i)) / 2
                            jac[i][:, idx] = [np.sin(w_l*i), 0, 0, A_f*i*np.cos(w_l*i), 0, 0]
                        else:
                            a[i][idx] = act_max * (1 + A_b*np.sin(w_l*i - phi_b)) / 2
                            jac[i][:, idx] = [0, np.sin(w_l*i), 0, A_b*i*np.cos(w_l*i-phi_b), 0, -A_b*np.cos(w_l*i - phi_b)]
                else:
                    for idx in indcs:
                        if key[0] =='F':
                            a[i][idx] =  act_max * (1 - A_f*np.sin(w_l*i)) / 2
                            jac[i][:, idx] = [-np.sin(w_l*i), 0, 0, -A_f*i*np.cos(w_l*i), 0, 0]
                        else:
                            a[i][idx] = act_max * (1 - A_b*np.sin(w_l*i - phi_b)) / 2
                            jac[i][:, idx] = [0, -np.sin(w_l*i), 0, -A_b*i*np.cos(w_l*i-phi_b), 0, A_b*np.cos(w_l*i - phi_b)]
            for key, indcs in spine_indices.items():
                if key == 'V':
                    for idx in indcs:
                        a[i][idx] = act_max * (1 + A_s*np.sin(w_l*i - phi_s)) / 2
                        jac[i][:, idx] = [0, 0, np.sin(w_l*i - phi_s), A_s*i*np.cos(w_l*i - phi_s), -A_s*np.cos(w_l*i - phi_s), 0]
                else:
                    for idx in indcs:
                        a[i][idx] = act_max * (1 - A_s*np.sin(w_l*i - phi_s)) / 2
                        jac[i][:, idx] = [0, 0, -np.sin(w_l*i - phi_s), -A_s*i*np.cos(w_l*i - phi_s), -A_s*np.cos(w_l*i - phi_s), 0]
        jac = [act_max * col / 2 for col in jac]
        if return_jac:
            return a, jac
        return a


    a_init = variable_to_states(x_init, False)
    env.simulate(dt, frame_num, methods[2], opts[2], q0, v0, a_init, f0, require_grad=False, vis_folder='init_gallop')

    bounds = scipy.optimize.Bounds(x_lb, x_ub)
    data = {}
    # for method, opt in zip(methods, opts):
    data[methods[2]] = []
    def loss_and_grad(x):
        a, jac = variable_to_states(x, True)
        loss, _, info = env.simulate(dt, frame_num, methods[2], opts[2], q0, v0, a, f0, require_grad=True, vis_folder=None)
        # Assemble the gradients.
        act_grad = info['actuator_gradients']
        grad = ndarray([jac[i].dot(np.transpose(act_grad[i])) for i in range(frame_num)])
        grad = np.sum(grad, axis=0)

        print('loss: {:8.3f}, |grad|: {:8.3f}, A_f: {:8.5f}, A_b: {:8.5f}, A_s: {:8.5f}, forward time: {:6.3f}s, backward time: {:6.3f}s'.format(
            loss, np.linalg.norm(grad), x[0], x[1], x[2], info['forward_time'], info['backward_time']))
        single_data = {}
        single_data['loss'] = loss
        single_data['grad'] = np.copy(grad)
        single_data['x'] = np.copy(x)
        single_data['forward_time'] = info['forward_time']
        single_data['backward_time'] = info['backward_time']
        data[methods[2]].append(single_data)
        return loss, np.copy(grad)

    # Use the two lines below to sanity check the gradients.
    # Note that you might need to fine tune the rel_tol in opt to make it work.
    # from py_diff_pd.common.grad_check import check_gradients
    # check_gradients(loss_and_grad, x_init, eps=1e-3)

    t0 = time.time()
    result = scipy.optimize.minimize(loss_and_grad, np.copy(x_init),
        method='L-BFGS-B', jac=True, bounds=bounds, options={ 'ftol': 1e-3 })
    t1 = time.time()
    print(result.success)
    x_final = result.x
    print_info('Optimizing with {} finished in {:6.3f} seconds'.format(methods[2], t1 - t0))
    pickle.dump(data, open(folder / 'data_{:04d}_threads.bin'.format(thread_ct), 'wb'))

    # Visualize results.
    a_final = variable_to_states(x_final, False)
    env.simulate(dt, frame_num, methods[2], opts[2], q0, v0, a_final, f0, require_grad=False, vis_folder=methods[2])