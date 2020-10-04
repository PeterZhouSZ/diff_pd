import time
from pathlib import Path
import os

import numpy as np

from py_diff_pd.env.env_base import EnvBase
from py_diff_pd.common.common import create_folder, ndarray
from py_diff_pd.common.mesh import generate_hex_mesh, get_contact_vertex
from py_diff_pd.common.display import render_hex_mesh, export_gif
from py_diff_pd.core.py_diff_pd_core import Mesh3d, Deformable3d, StdRealVector
from py_diff_pd.common.renderer import PbrtRenderer
from py_diff_pd.common.project_path import root_path
from py_diff_pd.common.mesh import hex2obj

class BunnyEnv3d(EnvBase):
    def __init__(self, seed, folder, options):
        EnvBase.__init__(self, folder)

        np.random.seed(seed)
        create_folder(folder, exist_ok=True)

        youngs_modulus = options['youngs_modulus'] if 'youngs_modulus' in options else 1e6
        poissons_ratio = options['poissons_ratio'] if 'poissons_ratio' in options else 0.49

        # Mesh parameters.
        la = youngs_modulus * poissons_ratio / ((1 + poissons_ratio) * (1 - 2 * poissons_ratio))
        mu = youngs_modulus / (2 * (1 + poissons_ratio))
        density = 1e3

        bin_file_name = Path(root_path) / 'asset' / 'mesh' / 'bunny_watertight.bin'
        mesh = Mesh3d()
        mesh.Initialize(str(bin_file_name))
        bunny_size = 0.1
        # Rescale the mesh.
        mesh.Scale(bunny_size)
        tmp_bin_file_name = '.tmp.bin'
        mesh.SaveToFile(tmp_bin_file_name)

        deformable = Deformable3d()
        deformable.Initialize(tmp_bin_file_name, density, 'none', youngs_modulus, poissons_ratio)
        os.remove(tmp_bin_file_name)
        # Elasticity.
        deformable.AddPdEnergy('corotated', [2 * mu,], [])
        deformable.AddPdEnergy('volume', [la,], [])
        # State-based forces.
        deformable.AddStateForce('gravity', [0, 0, -9.81])
        # Collisions.
        friction_node_idx = get_contact_vertex(mesh)
        # Uncomment the code below if you would like to display the contact set for a sanity check:
        '''
        import matplotlib.pyplot as plt
        from mpl_toolkits.mplot3d import Axes3D
        fig = plt.figure()
        ax = fig.add_subplot(111, projection='3d')
        v = ndarray([ndarray(mesh.py_vertex(idx)) for idx in friction_node_idx])
        ax.scatter(v[:, 0], v[:, 1], v[:, 2])
        plt.show()
        '''

        # Friction_node_idx = all vertices on the edge.
        deformable.SetFrictionalBoundary('planar', [0.0, 0.0, 1.0, 0.0], friction_node_idx)

        # Initial states.
        dofs = deformable.dofs()
        act_dofs = deformable.act_dofs()
        q0 = ndarray(mesh.py_vertices())
        v0 = np.zeros(dofs)
        f_ext = np.zeros(dofs)

        # Data members.
        self._deformable = deformable
        self._q0 = q0
        self._v0 = v0
        self._f_ext = f_ext
        self._youngs_modulus = youngs_modulus
        self._poissons_ratio = poissons_ratio
        self._stepwise_loss = False
        self._target_com = ndarray(options['target_com']) if 'target_com' in options else ndarray([0.15, 0.15, 0.15])
        self._bunny_size = bunny_size

        self.__spp = options['spp'] if 'spp' in options else 4

    def material_stiffness_differential(self, youngs_modulus, poissons_ratio):
        jac = self._material_jacobian(youngs_modulus, poissons_ratio)
        jac_total = np.zeros((2, 2))
        jac_total[0] = 2 * jac[1]
        jac_total[1] = jac[0]
        return jac_total

    def is_dirichlet_dof(self, dof):
        return False

    def _display_mesh(self, mesh_file, file_name):
        options = {
            'file_name': file_name,
            'light_map': 'uffizi-large.exr',
            'sample': self.__spp,
            'max_depth': 2,
            'camera_pos': (0.15, -1.75, 0.6),
            'camera_lookat': (0, .15, .4)
        }
        renderer = PbrtRenderer(options)

        mesh = Mesh3d()
        mesh.Initialize(mesh_file)

        scale = 3
        #Draw Wireframe of Bunny Mesh
        vertices, faces = hex2obj(mesh)
        for f in faces:
            for i in range(4):
                vi = vertices[f[i]]
                vj = vertices[f[(i + 1) % 4]]
                # Draw line vi to vj.
                renderer.add_shape_mesh({
                        'name': 'curve',
                        'point': ndarray([vi, (2 * vi + vj) / 3, (vi + 2 * vj) / 3, vj]),
                        'width': 0.001
                    },
                    color=(0.7, .5, 0.7),
                    transforms=[
                        ('s', scale)
                    ])
        renderer.add_tri_mesh(Path(root_path) / 'asset/mesh/curved_ground.obj',
            texture_img='chkbd_24_0.7', transforms=[('s', 2)])

        #Add target CoM and mesh CoM
        renderer.add_shape_mesh({ 'name': 'sphere', 'center': self._target_com, 'radius': 0.0075 },
            transforms=[('s', scale)], color=(0.1, 0.1, 0.9))

        com = np.mean(ndarray(mesh.py_vertices()).reshape((-1, 3)), axis=0)
        renderer.add_shape_mesh({ 'name': 'sphere', 'center': com, 'radius': 0.0075 },
            transforms=[('s', scale) ], color=(0.9, 0.1, 0.1))

        renderer.render()

    def _loss_and_grad(self, q, v):
        # Compute the center of mass.
        com = np.mean(q.reshape((-1, 3)), axis=0)
        # Compute loss.
        com_diff = com - self._target_com
        loss = 0.5 * com_diff.dot(com_diff) / (self._bunny_size ** 2)
        # Compute grad.
        grad_q = np.zeros(q.size)
        vertex_num = int(q.size // 3)
        for i in range(3):
            grad_q[i::3] = com_diff[i] / vertex_num / (self._bunny_size ** 2)
        grad_v = np.zeros(v.size) / (self._bunny_size ** 2)
        return loss, grad_q, grad_v
