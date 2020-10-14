import os
import struct
import numpy as np
from py_diff_pd.core.py_diff_pd_core import TetMesh3d
from py_diff_pd.common.common import ndarray, filter_unused_vertices

# use this function to generate *3D* tet meshes.
# vertices: n x 3 numpy array.
# faces: m x 4 numpy array.
def generate_tet_mesh(vertices, faces, bin_file_name):
    with open(bin_file_name, 'wb') as f:
        f.write(struct.pack('i', 3))
        f.write(struct.pack('i', 4))
        # Vertices.
        vert_num, _ = ndarray(vertices).shape
        f.write(struct.pack('i', 3))
        f.write(struct.pack('i', vert_num))
        for v in vertices:
            f.write(struct.pack('d', v[0]))
        for v in vertices:
            f.write(struct.pack('d', v[1]))
        for v in vertices:
            f.write(struct.pack('d', v[2]))

        # Faces.
        faces = ndarray(faces).astype(np.int)
        face_num, _ = faces.shape
        f.write(struct.pack('i', 4))
        f.write(struct.pack('i', face_num))
        for j in range(4):
            for i in range(face_num):
                f.write(struct.pack('i', faces[i, j]))

# Given four vertices of a tet, return a 4 x 3 int arrays of 0, 1, 2, and 3. Each row describes
# a surface triangle whose normal is pointing outward if you follow the vertices by the righ-hand rule.
def fix_tet_faces(verts):
    verts = ndarray(verts)
    v0, v1, v2, v3 = verts
    f = []
    if np.cross(v1 - v0, v2 - v1).dot(v3 - v0) < 0:
        f = [
            (0, 1, 2),
            (2, 1, 3),
            (1, 0, 3),
            (0, 2, 3),
        ]
    else:
        f = [
            (1, 0, 2),
            (1, 2, 3),
            (0, 1, 3),
            (2, 0, 3),
        ]

    return ndarray(f).astype(np.int)

# Given tet_mesh, return vert and faces that describes the surface mesh as a triangle mesh.
# You should use this function mostly for rendering.
# Output:
# - vertices: an n x 3 double array.
# - faces: an m x 3 integer array.
def tet2obj(tet_mesh, obj_file_name=None):
    vertex_num = tet_mesh.NumOfVertices()
    element_num = tet_mesh.NumOfElements()

    v = []
    for i in range(vertex_num):
        v.append(tet_mesh.py_vertex(i))
    v = ndarray(v)

    face_dict = {}
    for i in range(element_num):
        fi = list(tet_mesh.py_element(i))
        element_vert = []
        for vi in fi:
            element_vert.append(tet_mesh.py_vertex(vi))
        element_vert = ndarray(element_vert)
        face_idx = fix_tet_faces(element_vert)
        for f in face_idx:
            vidx = [int(fi[fij]) for fij in f]
            vidx_key = tuple(sorted(vidx))
            if vidx_key in face_dict:
                del face_dict[vidx_key]
            else:
                face_dict[vidx_key] = vidx

    f = []
    for _, vidx in face_dict.items():
        f.append(vidx)
    f = ndarray(f).astype(int)

    v, f = filter_unused_vertices(v, f)

    if obj_file_name is not None:
        with open(obj_file_name, 'w') as f_obj:
            for vv in v:
                f_obj.write('v {} {} {}\n'.format(vv[0], vv[1], vv[2]))
            for ff in f:
                f_obj.write('f {} {} {}\n'.format(ff[0] + 1, ff[1] + 1, ff[2] + 1))

    return v, f

# Extract boundary faces from a 3D mesh.
def get_boundary_face(tet_mesh):
    _, f = tet2obj(tet_mesh)
    return f

# Input:
# - verts: a 4 x 3 matrix.
# Output:
# - solid_angles: a 4d array where each element corresponds to the solid angle spanned by the other three vertices.
def compute_tet_angles(verts):
    partition = [
        ([1, 2, 3], 0),
        ([0, 2, 3], 1),
        ([0, 1, 3], 2),
        ([0, 1, 2], 3)
    ]
    verts = ndarray(verts)
    solid_angles = np.zeros(4)
    for (i0, i1, i2), apex_idx in partition:
        apex = verts[apex_idx]
        v0 = verts[i0]
        v1 = verts[i1]
        v2 = verts[i2]
        # https://en.wikipedia.org/wiki/Solid_angle#Tetrahedron.
        a_vec = v0 - apex
        b_vec = v1 - apex
        c_vec = v2 - apex
        a = np.linalg.norm(a_vec)
        b = np.linalg.norm(b_vec)
        c = np.linalg.norm(c_vec)
        tan_half_omega = a_vec.dot(np.cross(b_vec, c_vec)) / (
            a * b * c + a_vec.dot(b_vec) * c + a_vec.dot(c_vec) * b + b_vec.dot(c_vec) * a
        )
        solid_angles[apex_idx] = np.arctan(np.abs(tan_half_omega)) * 2
    return solid_angles

# Return a heuristic set of vertices that could be used for contact handling.
# - threshold: a vertex is considered to be a contact vertex if its solid angle < threshold.
def get_contact_vertex(tet_mesh, threshold=2 * np.pi):
    vertex_num = tet_mesh.NumOfVertices()
    element_num = tet_mesh.NumOfElements()

    v_solid_angle = np.zeros(vertex_num)
    for e in range(element_num):
        vertex_indices = list(tet_mesh.py_element(e))
        verts = []
        for vi in vertex_indices:
            verts.append(tet_mesh.py_vertex(vi))
        solid_angles = compute_tet_angles(verts)
        for vi, ai in zip(vertex_indices, solid_angles):
            v_solid_angle[vi] += ai

    contact_nodes = []
    for i, val in enumerate(v_solid_angle):
        if val < threshold:
            contact_nodes.append(i)
    return contact_nodes

# Input:
# - triangle_mesh_file_name (obj, ply, etc). It will be shifted and scaled so that it is bounded by [0, 1]^3.
# Output:
# - verts: an n x 3 double array of vertices.
# - elements: an m x 4 integer array of tets.
#   For each row in elements [i0, i1, i2, i3], we ensure that the normal of (i0, i1, i2) points outwards.
#   This also implies that i3 is on the other side of (i0, i1, i2).
import trimesh
import tetgen
import pyvista as pv

def tetrahedralize(triangle_mesh_file_name, visualize=False):
    tri_mesh = trimesh.load(triangle_mesh_file_name)
    assert tri_mesh.is_watertight
    bbx_offset = np.min(tri_mesh.vertices, axis=0)
    tri_mesh.vertices -= bbx_offset
    bbx_extent = ndarray(tri_mesh.bounding_box.extents)
    tri_mesh.vertices /= np.max(bbx_extent)
    # Now tri_mesh.vertices is bounded by [0, 1].
    tmp_file_name = '.tmp.stl'
    tri_mesh.export(tmp_file_name)
    mesh = pv.read(tmp_file_name)
    os.remove(tmp_file_name)
    if visualize:
        mesh.plot()

    tet = tetgen.TetGen(mesh)
    tet.make_manifold()
    nodes, elements = tet.tetrahedralize()

    if visualize:
        tet_grid = tet.grid
        # Plot half the tet.
        mask = tet_grid.points[:, 2] < 0.5
        half_tet = tet_grid.extract_points(mask)

        plotter = pv.Plotter()
        plotter.add_mesh(half_tet, color='w', show_edges=True)
        plotter.add_mesh(tet_grid, color='r', style='wireframe', opacity=0.2)
        plotter.show()

        plotter.close()

    # nodes is an n x 3 matrix.
    # elements is an m x 4 or m x 10 matrix. See this doc for details.
    # http://wias-berlin.de/software/tetgen/1.5/doc/manual/manual006.html#ff_ele.
    # In both cases, the first four columns of elements are the tets.
    nodes = ndarray(nodes)
    elements_unsigned = ndarray(elements).astype(np.int)[:, :4]

    # Fix the sign of elements if necessary.
    elements = []
    for e in elements_unsigned:
        v = ndarray([nodes[ei] for ei in e])
        v0, v1, v2, v3 = v
        if np.cross(v1 - v0, v2 - v1).dot(v3 - v0) < 0:
            elements.append(e)
        else:
            elements.append([e[0], e[2], e[1], e[3]])
    elements = ndarray(elements).astype(np.int)
    return filter_unused_vertices(nodes, elements)