import sys
sys.path.append('../')

from pathlib import Path
import shutil
import numpy as np

from py_diff_pd.core.py_diff_pd_core import StdRealVector, StdRealArray2d
from py_diff_pd.core.py_diff_pd_core import GravitationalStateForce2d, PlanarCollisionStateForce2d
from py_diff_pd.common.common import ndarray
from py_diff_pd.common.common import print_info, print_error, print_ok
from py_diff_pd.common.grad_check import check_gradients

def test_state_force_2d(verbose):
    # Uncomment the following line to try random seeds.
    #seed = np.random.randint(1e5)
    seed = 42
    if verbose:
        print_info('seed: {}'.format(seed))
    np.random.seed(seed)

    vertex_num = 10
    vertex_dim = 3
    dofs = vertex_dim * vertex_num
    q0 = np.random.normal(size=dofs)
    v0 = np.random.normal(size=dofs)
    f_weight = np.random.normal(size=dofs)

    gravity = GravitationalStateForce2d()
    g = StdRealArray2d()
    for i in range(2): g[i] = np.random.normal()
    gravity.PyInitialize(1.2, g)

    collision = PlanarCollisionStateForce2d()
    normal = StdRealArray2d()
    for i in range(2): normal[i] = np.random.normal()
    collision.PyInitialize(1.2, 0.34, normal, 0.56)

    eps = 1e-8
    atol = 1e-4
    rtol = 1e-2
    for state_force in [gravity, collision]:
        def l_and_g(x):
            return loss_and_grad(x, f_weight, state_force, dofs)
        if not check_gradients(l_and_g, np.concatenate([q0, v0]), eps, atol, rtol, verbose):
            if verbose:
                print_error('StateForce2d gradients mismatch.')
            return False

    return True

def loss_and_grad(qv, f_weight, state_force, dofs):
    q = qv[:dofs]
    v = qv[dofs:]
    f = ndarray(state_force.PyForwardForce(q, v))
    loss = f.dot(f_weight)

    # Compute gradients.
    dl_df = np.copy(f_weight)
    dl_dq = StdRealVector(dofs)
    dl_dv = StdRealVector(dofs)
    state_force.PyBackwardForce(q, v, f, dl_df, dl_dq, dl_dv)
    grad = np.concatenate([ndarray(dl_dq), ndarray(dl_dv)])
    return loss, grad

if __name__ == '__main__':
    verbose = True
    test_state_force_2d(verbose)