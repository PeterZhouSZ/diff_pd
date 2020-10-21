import sys
sys.path.append('../')
import os

from pathlib import Path
import time
import numpy as np
import scipy.optimize
import pickle
import matplotlib.pyplot as plt

from py_diff_pd.common.common import ndarray, create_folder
from py_diff_pd.common.common import print_info, print_ok, print_error, print_warning
from py_diff_pd.common.grad_check import check_gradients
from py_diff_pd.common.display import export_gif
from py_diff_pd.core.py_diff_pd_core import StdRealVector
from py_diff_pd.env.soft_starfish_env_3d import SoftStarfishEnv3d
from py_diff_pd.common.project_path import root_path

def check_hydro_gradients(deformable, x0):
    # Sanity check the gradients of the hydrodynamic forces.
    water_force_weight = np.random.normal(size=dofs)
    def loss_and_grad(x):
        q = x[:dofs]
        v = x[dofs:]
        water_force = ndarray(env._deformable.PyForwardStateForce(q, v))
        loss = water_force.dot(water_force_weight)
        grad_q = StdRealVector(dofs)
        grad_v = StdRealVector(dofs)
        deformable.PyBackwardStateForce(q, v, water_force, water_force_weight, grad_q, grad_v)
        grad = np.zeros(2 * dofs)
        grad[:dofs] = ndarray(grad_q)
        grad[dofs:] = ndarray(grad_v)
        return loss, grad

    eps = 1e-8
    atol = 1e-4
    rtol = 1e-2
    grads_equal = check_gradients(loss_and_grad, x0, eps, rtol=rtol, atol=atol, verbose=True)
    if not grads_equal:
        print_error('ForwardStateForce and BackwardStateForce do not match.')

def visualize_hydro(deformable, bin_mesh_file, img_file, q, v):
    from py_diff_pd.common.renderer import PbrtRenderer
    from py_diff_pd.common.project_path import root_path
    from py_diff_pd.common.tet_mesh import TetMesh3d
    options = {
        'file_name': img_file,
        'light_map': 'uffizi-large.exr',
        'sample': 64,
        'max_depth': 2,
        'camera_pos': (0.15, -0.75, 1.4),
        'camera_lookat': (0, .15, .4)
    }
    renderer = PbrtRenderer(options)

    mesh = TetMesh3d()
    mesh.Initialize(str(bin_mesh_file))
    transforms = [
        ('t', (-0.075, -0.075, 0)),
        ('s', 3),
        ('t', (0.2, 0.4, 0.2))
    ]
    #renderer.add_tri_mesh(mesh, color=(.6, .3, .2),
    #    transforms=transforms, render_tet_edge=False,
    #)

    # Render water force.
    hydro_force = deformable.PyForwardStateForce(q, v)
    f = np.reshape(ndarray(hydro_force), (-1, 3))
    q = np.reshape(ndarray(mesh.py_vertices()), (-1, 3))
    for fi, qi in zip(f, q):
        scale = 1.0
        v0 = qi
        v3 = scale * fi + qi
        v1 = (2 * v0 + v3) / 3
        v2 = (v0 + 2 * v3) / 3
        if np.linalg.norm(fi) == 0: continue
        renderer.add_shape_mesh({
                'name': 'curve',
                'point': ndarray([v0, v1, v2, v3]),
                'width': 0.001
            },
            color=(.2, .6, .3),
            transforms=transforms
        )

    renderer.add_tri_mesh(Path(root_path) / 'asset/mesh/curved_ground.obj',
        texture_img='chkbd_24_0.7', transforms=[('s', 2)])

    renderer.render()

def load_csv_data(csv_name):
    csv_name = Path(csv_name)
    data = {}
    with open(csv_name, 'r') as f:
        lines = f.readlines()
    # Line 0 is the header.
    data['time'] = []
    data['dl'] = []
    data['M1'] = []
    data['M2'] = []
    data['M3'] = []
    data['M4'] = []
    for l in lines[1:]:
        l = l.strip()
        if l == '': continue
        item = [float(v) for v in l.split(',') if v != '']
        assert len(item) == 10
        if np.isnan(item[1]) or item[1] == 0:
            continue
        t, dl, m1x, m1y, m2x, m2y, m3x, m3y, m4x, m4y = item
        data['time'].append(t)
        data['dl'].append(dl)
        data['M1'].append((m1x, m1y))
        data['M2'].append((m2x, m2y))
        data['M3'].append((m3x, m3y))
        data['M4'].append((m4x, m4y))
    # Normalize data.
    t = ndarray(data['time'])
    t -= t[0]
    t /= 1000
    data['time'] = t    # Now t is in the unit of seconds.
    dt = t[1:] - t[:-1]
    assert np.max(dt) - np.min(dt) < 1e-4 and np.abs(np.mean(dt) - 1 / 60) < 1e-4
    data['dt'] = np.mean(dt)
    dl = ndarray(data['dl'])
    dl /= 1000
    data['dl'] = dl     # Now dl is in the unit of meters.
    for i in range(1, 5):
        name = 'M{:d}'.format(i)
        mi_pos = ndarray(data[name])
        mi_pos /= 1000  # Now mi_pos is in the unit of meters.
        mi_pos -= mi_pos[0]
        # Convert the coordinates:
        # x -> x
        # y -> -z.
        data[name + '_rel_x'] = mi_pos[:, 0]
        data[name + '_rel_z'] = -mi_pos[:, 1]
    del data['M1']
    del data['M2']
    del data['M3']
    del data['M4']
    return data

if __name__ == '__main__':
    seed = 42
    np.random.seed(seed)
    folder = Path('soft_starfish_3d')
    measurement_data = load_csv_data(Path(root_path) / 'python/example' / folder / 'data_horizontal_cyclic1.csv')

    youngs_modulus = 5e5
    poissons_ratio = 0.4
    act_stiffness = 2e6
    substep = 5
    env = SoftStarfishEnv3d(seed, folder, {
        'youngs_modulus': youngs_modulus,
        'poissons_ratio': poissons_ratio,
        'act_stiffness': act_stiffness,
        'y_actuator': False,
        'z_actuator': True,
        'fix_center_x': False,
        'fix_center_y': True,
        'fix_center_z': True,
        'use_stepwise_loss': True,
        'data': measurement_data,
        'substep': substep
    })
    deformable = env.deformable()

    # Optimization parameters.
    newton_method = 'newton_pcg'
    pd_method = 'pd_eigen'
    thread_ct = 6
    newton_opt = { 'max_newton_iter': 500, 'max_ls_iter': 10, 'abs_tol': 1e-9, 'rel_tol': 1e-4,
        'verbose': 0, 'thread_ct': thread_ct }
    pd_opt = { 'max_pd_iter': 500, 'max_ls_iter': 10, 'abs_tol': 1e-9, 'rel_tol': 1e-4,
        'verbose': 0, 'thread_ct': thread_ct, 'use_bfgs': 1, 'bfgs_history_size': 10 }

    # Try out random control signals.
    dofs = deformable.dofs()
    act_dofs = deformable.act_dofs()
    q0 = env.default_init_position()
    v0 = np.zeros(dofs)
    dt = measurement_data['dt'] / substep
    frame_num = 1 * substep
    a0 = [np.random.uniform(low=0.69, high=0.71, size=act_dofs) for _ in range(frame_num)]
    f0 = np.zeros(dofs)
    f0 = [f0 for _ in range(frame_num)]
    _, info = env.simulate(dt, frame_num, pd_method, pd_opt, q0, v0, a0, f0, require_grad=False,
        vis_folder='random')

    # Uncomment the following lines to check gradients.
    '''
    q_final = ndarray(info['q'][-1])
    v_final = ndarray(info['v'][-1])
    x0 = np.concatenate([q_final, v_final])
    check_hydro_gradients(deformable, x0)
    create_folder(folder / 'vis')
    for i in range(frame_num + 1):
        q = ndarray(info['q'][i])
        v = ndarray(info['v'][i])
        visualize_hydro(deformable, folder / 'random/{:04d}.bin'.format(i),
            folder / 'vis/{:04d}.png'.format(i), q, v)
    '''

    ###########################################################################
    # System identification
    ###########################################################################
    # Conmpute actuation signals.
    actuator_signals = []
    frame_num = 60 * substep
    f0 = np.zeros(dofs)
    f0 = [f0 for _ in range(frame_num)]
    for i in range(frame_num):
        actuator_signal = 1 - np.ones(act_dofs) * measurement_data['dl'][int(i // substep)] / env.full_tendon_length()
        actuator_signals.append(actuator_signal)

    # Range of the material parameters.
    # Decision variables: log(E), log(nu).
    x_lb = ndarray([np.log(1e4), np.log(0.2)])
    x_ub = ndarray([np.log(5e6), np.log(0.45)])
    x_init = np.random.uniform(x_lb, x_ub)
    bounds = scipy.optimize.Bounds(x_lb, x_ub)

    # Normalize the loss.
    random_guess_num = 4
    x_rands = [np.random.uniform(low=x_lb, high=x_ub) for _ in range(random_guess_num)]
    random_loss = []
    best_loss = np.inf
    for x_rand in x_rands:
        E = np.exp(x_rand[0])
        nu = np.exp(x_rand[1])
        env_opt = SoftStarfishEnv3d(seed, folder, {
            'youngs_modulus': E,
            'poissons_ratio': nu,
            'act_stiffness': act_stiffness,
            'y_actuator': False,
            'z_actuator': True,
            'fix_center_x': False,
            'fix_center_y': True,
            'fix_center_z': True,
            'use_stepwise_loss': True,
            'data': measurement_data,
            'substep': substep
        })
        loss, _ = env_opt.simulate(dt, frame_num, pd_method, pd_opt, q0, v0, actuator_signals, f0,
            require_grad=False, vis_folder=None)
        print('E: {:3e}, nu: {:3f}, loss: {:3f}'.format(E, nu, loss))
        random_loss.append(loss)
        if loss < best_loss:
            best_loss = loss
            x_init = np.copy(x_rand)
    loss_range = ndarray([0, np.mean(random_loss)])
    unit_loss = np.mean(random_loss)
    print_info('Loss range: {:3f}, {:3f}'.format(loss_range[0], loss_range[1]))

    data = { 'loss_range': loss_range }
    data[pd_method] = []
    def loss_and_grad(x):
        E = np.exp(x[0])
        nu = np.exp(x[1])
        env_opt = SoftStarfishEnv3d(seed, folder, {
            'youngs_modulus': E,
            'poissons_ratio': nu,
            'act_stiffness': act_stiffness,
            'y_actuator': False,
            'z_actuator': True,
            'fix_center_x': False,
            'fix_center_y': True,
            'fix_center_z': True,
            'use_stepwise_loss': True,
            'data': measurement_data,
            'substep': substep
        })
        loss, _, info = env_opt.simulate(dt, frame_num, (pd_method, newton_method),
            (pd_opt, newton_opt), q0, v0, actuator_signals, f0, require_grad=True, vis_folder=None)
        grad = info['material_parameter_gradients']
        grad = grad * np.exp(x)
        average_dist = np.sqrt(loss / 4 / frame_num) * 1000
        # Normalize the loss.
        loss /= unit_loss
        grad /= unit_loss
        print('loss: {:3.6e}, |grad|: {:3.6e}, dist: {:3.6f} mm, E: {:3.6e}, nu: {:3.6f}, forward: {:3.6f} s, backward: {:3.6f} s'.format(
            loss, np.linalg.norm(grad), average_dist, E, nu, info['forward_time'], info['backward_time']))
        return loss, grad

    # File index + 1 = len(opt_history).
    loss, grad = loss_and_grad(x_init)
    opt_history = [(x_init.copy(), loss, grad.copy())]
    pickle.dump(opt_history, open(folder / '{:04d}.data'.format(0), 'wb'))
    def callback(x):
        loss, grad = loss_and_grad(x)
        global opt_history
        cnt = len(opt_history)
        print_info('Summary of iteration {:4d}'.format(cnt))
        opt_history.append((x.copy(), loss, grad.copy()))
        print_info('loss: {:3.6e}, |grad|: {:3.6e}, |x|: {:3.6e}'.format(
            loss, np.linalg.norm(grad), np.linalg.norm(x)))
        # Save data to the folder.
        pickle.dump(opt_history, open(folder / '{:04d}.data'.format(cnt), 'wb'))

    results = scipy.optimize.minimize(loss_and_grad, x_init.copy(), method='L-BFGS-B', jac=True, bounds=bounds,
        callback=callback, options={ 'ftol': 1e-8, 'maxiter': 20 })
    if not results.success:
        print_warning('Local optimization fails to reach the optimal condition and will return the last solution.')
    print_info('Data saved to {}/{:04d}.data.'.format(str(folder), len(opt_history) - 1))
    x_final = results.x

    # Visualize results.
    E = np.exp(x_final[0])
    nu = np.exp(x_final[1])
    env_opt = SoftStarfishEnv3d(seed, folder, {
        'youngs_modulus': E,
        'poissons_ratio': nu,
        'act_stiffness': act_stiffness,
        'y_actuator': False,
        'z_actuator': True,
        'fix_center_x': False,
        'fix_center_y': True,
        'fix_center_z': True,
        'use_stepwise_loss': True,
        'data': measurement_data,
        'substep': substep
    })
    env_opt.simulate(dt, frame_num, pd_method, pd_opt, q0, v0, actuator_signals, f0,
        require_grad=False, vis_folder=pd_method)
    export_gif(folder / pd_method, '{}.gif'.format(pd_method), fps=int(1 / dt))

    # Visualize the progress.
    cnt = 0
    while True:
        data_file_name = folder / '{:04d}.data'.format(cnt)
        if not os.path.exists(data_file_name):
            cnt -= 1
            break
        cnt += 1
    data_file_name = folder / '{:04d}.data'.format(cnt)
    print_info('Loading data from {}.'.format(data_file_name))
    opt_history = pickle.load(open(data_file_name, 'rb'))

    # Plot the optimization progress.
    plt.rc('pdf', fonttype=42)
    plt.rc('font', size=18)
    plt.rc('axes', titlesize=18)
    plt.rc('axes', labelsize=18)

    fig = plt.figure(figsize=(18, 12))
    ax_loss = fig.add_subplot(121)
    ax_grad = fig.add_subplot(122)

    ax_loss.set_position((0.12, 0.2, 0.33, 0.6))
    iterations = np.arange(len(opt_history))
    ax_loss.plot(iterations, [l for _, l, _ in opt_history], color='tab:red')
    ax_loss.set_xlabel('Iteration')
    ax_loss.set_ylabel('Loss')
    ax_loss.set_yscale('log')
    ax_loss.grid(True, which='both')

    ax_grad.set_position((0.55, 0.2, 0.33, 0.6))
    ax_grad.plot(iterations, [np.linalg.norm(g) + np.finfo(np.float).eps for _, _, g in opt_history],
        color='tab:green')
    ax_grad.set_xlabel('Iteration')
    ax_grad.set_ylabel('|Gradient|')
    ax_grad.set_yscale('log')
    ax_grad.grid(True, which='both')

    plt.show()
    fig.savefig(folder / 'progress.pdf')

    '''
    # Optimization.
    frame_num = 120
    control_skip_frame_num = 1
    control_frame_num = int(frame_num // control_skip_frame_num)
    var_dofs = 2 * (control_frame_num + 1)
    x_low = np.zeros(var_dofs)
    x_high = np.ones(var_dofs)
    bounds = scipy.optimize.Bounds(x_low, x_high)
    x_init = np.random.uniform(x_low, x_high)
    def variable_to_act(x):
        u_full = []
        half_act_dofs = int(act_dofs // 2)
        for i in range(control_frame_num):
            ui_begin = x[2 * i:2 * i + 2]
            ui_end = x[2 * i + 2:2 * i + 4]
            for j in range(control_skip_frame_num):
                t = j / control_skip_frame_num
                ui = (1 - t) * ui_begin + t * ui_end
                u = np.zeros(act_dofs)
                u[:half_act_dofs] = ui[0]
                u[half_act_dofs:] = ui[1]
                u_full.append(u)
        return u_full

    f0 = np.zeros((frame_num, dofs))
    data = []
    def loss_and_grad(x):
        u_full = variable_to_act(x)
        loss, grad, info = env.simulate(dt, frame_num, (pd_method, newton_method), (pd_opt, newton_opt),
            q0, v0, u_full, f0, require_grad=True, vis_folder=None)
        grad_u_full = grad[2]
        grad_x = np.zeros(x.size)
        half_act_dofs = int(act_dofs // 2)
        for i in range(control_frame_num):
            for j in range(control_skip_frame_num):
                t = j / control_skip_frame_num
                grad_u = grad_u_full[i * control_skip_frame_num + j]
                grad_x[2 * i] += (1 - t) * np.sum(grad_u[:half_act_dofs])
                grad_x[2 * i + 1] += (1 - t) * np.sum(grad_u[half_act_dofs:])
                grad_x[2 * i + 2] += t * np.sum(grad_u[:half_act_dofs])
                grad_x[2 * i + 3] += t * np.sum(grad_u[half_act_dofs:])
        print('loss: {:8.3f}, |grad|: {:8.3f}, forward time: {:6.3f}s, backward time: {:6.3f}s'.format(
            loss, np.linalg.norm(grad_x), info['forward_time'], info['backward_time']))
        single_data = {}
        single_data['loss'] = loss
        single_data['grad'] = np.copy(grad)
        single_data['x'] = np.copy(x)
        single_data['forward_time'] = info['forward_time']
        single_data['backward_time'] = info['backward_time']
        data.append(single_data)
        return loss, grad_x

    eps = 1e-8
    atol = 1e-4
    rtol = 1e-2
    grads_equal = check_gradients(loss_and_grad, x_init, eps, rtol=rtol, atol=atol, verbose=True)
    if not grads_equal:
        print_error('ForwardStateForce and BackwardStateForce do not match.')

    t0 = time.time()
    result = scipy.optimize.minimize(loss_and_grad, np.copy(x_init),
        method='L-BFGS-B', jac=True, bounds=bounds, options={ 'ftol': 1e-3, 'maxiter': 10 })
    t1 = time.time()
    print(result.success)
    x_final = result.x
    pickle.dump(data, open(folder / 'data_{:04d}_threads.bin'.format(thread_ct), 'wb'))

    # Visualize results.
    a_final = variable_to_act(x_final)
    env.simulate(dt, frame_num, pd_method, pd_opt, q0, v0, a_final, f0, require_grad=False, vis_folder='final')
    '''