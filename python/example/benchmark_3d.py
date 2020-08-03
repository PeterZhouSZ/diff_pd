import sys
sys.path.append('../')

import os
import subprocess
from pathlib import Path
import time
from pathlib import Path
import pickle
import scipy.optimize
import numpy as np

from py_diff_pd.common.common import ndarray, create_folder
from py_diff_pd.common.common import print_info, PrettyTabular
from py_diff_pd.env.benchmark_env_3d import BenchmarkEnv3d

def test_benchmark_3d(verbose):
    seed = 42
    folder = Path('benchmark_3d')
    env = BenchmarkEnv3d(seed, folder, { 'refinement': 1 })
    deformable = env.deformable()

    # Setting thread number.
    max_threads = int(subprocess.run(['nproc', '--all'], capture_output=True).stdout)
    thread_cts = [2, 4, 8]

    methods = ('newton_pcg', 'newton_cholesky', 'pd_eigen', 'pd_no_bfgs')
    opts = ({ 'max_newton_iter': 5000, 'max_ls_iter': 10, 'abs_tol': 1e-9, 'rel_tol': 1e-4, 'verbose': 0, 'thread_ct': 4 },
        { 'max_newton_iter': 5000, 'max_ls_iter': 10, 'abs_tol': 1e-9, 'rel_tol': 1e-4, 'verbose': 0, 'thread_ct': 4 },
        # Disable line search in PD: we are using quadratic material models.
        { 'max_pd_iter': 5000, 'max_ls_iter': 1, 'abs_tol': 1e-9, 'rel_tol': 1e-4, 'verbose': 0, 'thread_ct': 4,
            'use_bfgs': 1, 'bfgs_history_size': 10 },
        { 'max_pd_iter': 5000, 'max_ls_iter': 1, 'abs_tol': 1e-9, 'rel_tol': 1e-4, 'verbose': 0, 'thread_ct': 4,
            'use_bfgs': 0, 'bfgs_history_size': 10 })

    dofs = deformable.dofs()
    act_dofs = deformable.act_dofs()
    q0 = env.default_init_position()
    v0 = env.default_init_velocity()
    a0 = np.random.uniform(size=act_dofs)
    f0 = np.random.normal(scale=0.1, size=dofs) * 1e-3

    # Visualization.
    dt = 1e-2
    frame_num = 25
    if verbose:
        for method, opt in zip(methods, opts):
            env.simulate(dt, frame_num, method, opt, q0, v0, [a0 for _ in range(frame_num)],
                [f0 for _ in range(frame_num)], require_grad=False, vis_folder=method)
            os.system('eog {}.gif'.format(folder / method))

    # Benchmark time.
    print('Reporting time cost. DoFs: {:d}, frames: {:d}, dt: {:3.3e}'.format(dofs, frame_num, dt))
    rel_tols = [1e-1, 1e-2, 1e-3, 1e-4, 1e-5, 1e-6, 1e-7, 1e-8]
    forward_backward_times = {}
    forward_times = {}
    backward_times = {}
    losses = {}
    grads = {}
    for method in methods:
        for thread_ct in thread_cts:
            meth_thread_num = '{}_{}threads'.format(method, thread_ct)
            forward_backward_times[meth_thread_num] = []
            forward_times[meth_thread_num] = []
            backward_times[meth_thread_num] = []
            losses[meth_thread_num] = []
            grads[meth_thread_num] = []

    for rel_tol in rel_tols:
        print_info('rel_tol: {:3.3e}'.format(rel_tol))
        tabular = PrettyTabular({
            'method': '{:^30s}',
            'forward and backward (s)': '{:3.3f}',
            'forward only (s)': '{:3.3f}',
            'loss': '{:3.3f}',
            '|grad|': '{:3.3f}'
        })
        print_info(tabular.head_string())

        for method, opt in zip(methods, opts):
            opt['rel_tol'] = rel_tol
            if method == 'pd_no_bfgs':
                method = 'pd_eigen'
            for thread_ct in thread_cts:
                opt['thread_ct'] = thread_ct
                if 'pd' in method and not opt['use_bfgs']:
                    meth_thread_num = 'pd_no_bfgs_{}threads'.format(thread_ct)
                else:
                    meth_thread_num = '{}_{}threads'.format(method, thread_ct)

                loss, grad, info = env.simulate(dt, frame_num, method, opt, q0, v0, [a0 for _ in range(frame_num)],
                    [f0 for _ in range(frame_num)], require_grad=True, vis_folder=None)
                grad_q, grad_v, grad_a, grad_f = grad
                grad = np.zeros(q0.size + v0.size + a0.size + f0.size)
                grad[:dofs] = grad_q
                grad[dofs:2 * dofs] = grad_v
                grad[2 * dofs:2 * dofs + act_dofs] = np.sum(ndarray(grad_a), axis=0)
                grad[2 * dofs + act_dofs:] = np.sum(ndarray(grad_f), axis=0)
                l, g, forward_time, backward_time = loss, grad, info['forward_time'], info['backward_time']
                print(tabular.row_string({
                    'method': meth_thread_num,
                    'forward and backward (s)': forward_time + backward_time,
                    'forward only (s)': forward_time,
                    'loss': l,
                    '|grad|': np.linalg.norm(g) }))
                forward_backward_times[meth_thread_num].append(forward_time + backward_time)
                forward_times[meth_thread_num].append(forward_time)
                backward_times[meth_thread_num].append(backward_time)
                losses[meth_thread_num].append(l)
                grads[meth_thread_num].append(g)
        pickle.dump((rel_tols, forward_times, backward_times, losses, grads), open(folder / 'table.bin', 'wb'))


if __name__ == '__main__':
    verbose = False
    test_benchmark_3d(verbose)
