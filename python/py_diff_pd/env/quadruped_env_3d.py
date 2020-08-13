import time
from pathlib import Path

import numpy as np

from py_diff_pd.env.env_base import EnvBase
from py_diff_pd.common.common import create_folder, ndarray
from py_diff_pd.common.mesh import generate_hex_mesh
from py_diff_pd.common.display import render_hex_mesh, export_gif
from py_diff_pd.core.py_diff_pd_core import Mesh3d, Deformable3d, StdRealVector

class QuadrupedEnv3d(EnvBase):
    # Refinement is an integer controlling the resolution of the mesh.
    def __init__(self, seed, folder, options):
        EnvBase.__init__(self, folder)

        np.random.seed(seed)
        create_folder(folder, exist_ok=True)

        youngs_modulus = options['youngs_modulus']
        poissons_ratio = options['poissons_ratio']
        # Configure the size of the quadruped:
        # Foot width = 1.
        foot_width_size = 0.1
        leg_z_length = options['leg_z_length']
        body_x_length = options['body_x_length']
        body_y_length = options['body_y_length']
        body_z_length = options['body_z_length']
        refinement = options['refinement']
        assert leg_z_length >= 1
        assert body_x_length >= 3
        assert body_y_length >= 3
        assert body_z_length >= 1
        # Refinement defines how many cells does food_width have.
        assert refinement >= 2
        dx = foot_width_size / refinement

        # Mesh parameters.
        la = youngs_modulus * poissons_ratio / ((1 + poissons_ratio) * (1 - 2 * poissons_ratio))
        mu = youngs_modulus / (2 * (1 + poissons_ratio))
        density = 1e3
        cell_nums = (refinement * body_x_length, refinement * body_y_length, refinement * (leg_z_length + body_z_length))
        origin = ndarray([0, 0, 0])
        node_nums = [n + 1 for n in cell_nums]

        bin_file_name = folder / 'mesh.bin'
        # Topology.
        voxels = np.zeros(cell_nums)
        for i in range(cell_nums[0]):
            for j in range(cell_nums[1]):
                # Body.
                for k in range(refinement * leg_z_length, cell_nums[2]):
                    voxels[i][j][k] = 1
                # Legs.
                for k in range(refinement * leg_z_length):
                    if (i < refinement and j < refinement) or (i < refinement and j >= body_y_length * refinement - refinement) or \
                        (i >= body_x_length * refinement - refinement and j < refinement) or \
                        (i >= body_x_length * refinement - refinement and j >= body_y_length * refinement - refinement):
                        voxels[i][j][k] = 1
        generate_hex_mesh(voxels, dx, origin, bin_file_name)
        mesh = Mesh3d()
        mesh.Initialize(str(bin_file_name))

        deformable = Deformable3d()
        deformable.Initialize(str(bin_file_name), density, 'none', youngs_modulus, poissons_ratio)
        # State-based forces.
        deformable.AddStateForce('gravity', [0, 0, -9.81])
        # Elasticity.
        deformable.AddPdEnergy('corotated', [2 * mu,], [])
        deformable.AddPdEnergy('volume', [la,], [])
        # Collisions.
        vertex_num = mesh.NumOfVertices()
        friction_node_idx = []
        for i in range(vertex_num):
            vx, vy, vz = mesh.py_vertex(i)
            if vz < dx / 2:
                vx_idx = vx / dx
                vy_idx = vy / dx
                if vx_idx < 0.5 or np.abs(vx_idx - refinement) < 0.5 or \
                    np.abs(vx_idx - (body_x_length - refinement)) < 0.5 or np.abs(vx_idx - body_x_length) < 0.5 or \
                    vy_idx < 0.5 or np.abs(vy_idx - refinement) < 0.5 or \
                    np.abs(vy_idx - (body_y_length - refinement)) < 0.5 or np.abs(vy_idx - body_y_length) < 0.5:
                    friction_node_idx.append(i)
        deformable.SetFrictionalBoundary('planar', [0.0, 0.0, 1.0, 0.0], friction_node_idx)
        # Actuation: we have 4 legs and each leg has 4 muscles.
        # Convention: F (Front): positive x; R (Rear): negative x;
        #             L (Left): positive y; R (Right): negative y.
        act_indices = {}
        element_num = mesh.NumOfElements()
        for i in range(element_num):
            v_idx = ndarray(mesh.py_element(i))
            # Obtain the center of this cell.
            com = 0
            for vi in v_idx:
                com += ndarray(mesh.py_vertex(int(vi)))
            com /= len(v_idx)
            x_idx, y_idx, z_idx = com / dx
            if z_idx >= leg_z_length * refinement: continue
            # First, determine which leg the voxel is in.
            leg_key = ('F' if x_idx >= body_x_length * 0.5 else 'R') \
                + ('L' if y_idx >= body_y_length * 0.5 else 'R')
            # Second, determine which muscle this voxel should be in.
            if leg_key[0] == 'F':
                x_idx -= body_x_length - refinement
            if leg_key[1] == 'L':
                y_idx -= body_y_length - refinement
            muscle_key = ('F' if x_idx >= 0.5 * refinement else 'R') \
                + ('L' if y_idx >= 0.5 * refinement else 'R')
            key = leg_key + muscle_key
            if key not in act_indices:
                act_indices[key] = [i,]
            else:
                act_indices[key].append(i)
        for key, act_idx in act_indices.items():
            deformable.AddActuation(1e5, [0.0, 0.0, 1.0], act_idx)

        # Initial conditions.
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
        self.__node_nums = node_nums

    def material_stiffness_differential(self, youngs_modulus, poissons_ratio):
        jac = self._material_jacobian(youngs_modulus, poissons_ratio)
        jac_total = np.zeros((2, 2))
        jac_total[0] = 2 * jac[1]
        jac_total[1] = jac[0]
        return jac_total

    def is_dirichlet_dof(self, dof):
        return False

    def _display_mesh(self, mesh_file, file_name):
        mesh = Mesh3d()
        mesh.Initialize(mesh_file)
        render_hex_mesh(mesh, file_name=file_name,
            resolution=(400, 400), sample=8, transforms=[
                ('s', 1.5)
            ])

    def _loss_and_grad(self, q, v):
        # Compute the center of mass.
        com = np.mean(q.reshape((-1, 3)), axis=0)
        loss = -com[0]
        grad_q = np.zeros(q.size)
        vertex_num = int(q.size // 3)
        grad_q[::3] = -1 / vertex_num

        grad_v = np.zeros(v.size)
        return loss, grad_q, grad_v