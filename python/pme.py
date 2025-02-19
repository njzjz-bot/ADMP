from python.neiV3 import construct_nblist
import numpy as np
import jax.numpy as jnp
from jax.scipy.special import erf
from jax.config import config
from jax.api import jit
config.update("jax_enable_x64", True)


dielectric = 1389.35455846 # in e^2/A

def generate_construct_localframes(axis_types, axis_indices):
    """
    Generates the local frame constructor, common to the same physical system
    inputs:
        axis_types:
            types of local frame transformation rules for each atom.
        axis_indices:
            z,x,y atoms of each local frame.
    outputs:
        construct_localframes:
            function type (positions, box) -> local_frames
    """
    ZThenX            = 0
    Bisector          = 1
    ZBisect           = 2
    ThreeFold         = 3
    Zonly             = 4
    NoAxisType        = 5
    LastAxisTypeIndex = 6
    
    z_atoms = jnp.array(axis_indices[:, 0])
    x_atoms = jnp.array(axis_indices[:, 1])
    y_atoms = jnp.array(axis_indices[:, 2])
    
    Zonly_filter = (axis_types == Zonly)
    not_Zonly_filter = jnp.logical_not(Zonly_filter)
    Bisector_filter = (axis_types == Bisector)
    ZBisect_filter = (axis_types == ZBisect)
    ThreeFold_filter = (axis_types == ThreeFold)
    
    def pbc_shift(drvecs, box, box_inv):
        '''
        Dealing with the pbc shifts of vectors

        Inputs:
            rvecs:
                N * 3, a list of real space vectors in Cartesian
            box:
                3 * 3, box matrix, with axes arranged in rows
            box_inv:
                3 * 3, inverse of box matrix

        Outputs:
            rvecs:
                N * 3, vectors that have been shifted, in Cartesian
        '''
        unshifted_dsvecs = drvecs.dot(box_inv)
        dsvecs = unshifted_dsvecs - jnp.floor(unshifted_dsvecs + 0.5)
        return dsvecs.dot(box)
        
    def normalize(matrix, axis=1, ord=2):
        '''
        Normalise a matrix along one dimension
        '''
        normalised = matrix / jnp.linalg.norm(matrix, axis=axis, keepdims=True, ord=ord)
        return normalised

    def c_l_f(positions, box):
        '''
        This function constructs the local frames for each site

        Inputs:
            positions:
                N * 3: the positions matrix
            box:
        Outputs:
            Q: 
                N*(lmax+1)^2, the multipole moments in global harmonics.
            local_frames:
                N*3*3, the local frames, axes arranged in rows
        '''

        positions = jnp.array(positions)
        n_sites = positions.shape[0]
        box_inv = jnp.linalg.inv(box)

        ### Process the x, y, z vectors according to local axis rules
        vec_z = pbc_shift(positions[z_atoms] - positions, box, box_inv)
        vec_z = normalize(vec_z)
        vec_x = jnp.zeros((n_sites, 3))
        vec_y = jnp.zeros((n_sites, 3))
        # Z-Only
        x_of_vec_z = jnp.round(jnp.abs(vec_z[:,0]))
        vec_x_Zonly = jnp.array([1.-x_of_vec_z, x_of_vec_z, jnp.zeros_like(x_of_vec_z)]).T
        vec_x = vec_x.at[Zonly_filter].set(vec_x_Zonly)
        # for those that are not Z-Only, get normalized vecX
        vec_x_not_Zonly = positions[x_atoms[not_Zonly_filter]] - positions[not_Zonly_filter]
        vec_x_not_Zonly = pbc_shift(vec_x_not_Zonly, box, box_inv)

        vec_x = vec_x.at[not_Zonly_filter].set(normalize(vec_x_not_Zonly, axis=1))
        # Bisector
        if np.sum(Bisector_filter) > 0:
            vec_z_Bisector = vec_z[Bisector_filter] + vec_x[Bisector_filter]
            vec_z = vec_z.at[Bisector_filter].set(normalize(vec_z_Bisector, axis=1))
        # z-bisector
        if np.sum(ZBisect_filter) > 0:
            vec_y_ZBisect = positions[y_atoms[ZBisect_filter]] - positions[ZBisect_filter]
            vec_y_ZBisect = pbc_shift(vec_y_ZBisect, box, box_inv)
            vec_y_ZBisect = normalize(vec_y_ZBisect, axis=1)
            vec_x_ZBisect = vec_x[ZBisect_filter] + vec_y_ZBisect
            vec_x = vec_x.at[ZBisect_filter].set(normalize(vec_x_ZBisect, axis=1))
        # ThreeFold
        if np.sum(ThreeFold_filter) > 0:
            vec_x_threeFold = vec_x[ThreeFold_filter]
            vec_z_threeFold = vec_z[ThreeFold_filter]
            
            vec_y_threeFold = positions[y_atoms[ThreeFold_filter]] - positions[ThreeFold_filter]
            vec_y_threeFold = pbc_shift(vec_y_threeFold, box, box_inv)
            vec_y_threeFold = normalize(vec_y_threeFold, axis=1)
            vec_z_threeFold += (vec_x_threeFold + vec_y_threeFold)
            vec_z_threeFold = normalize(vec_z_threeFold)
            
            vec_y = vec_y.at[ThreeFold_filter].set(vec_y_threeFold)
            vec_z = vec_z.at[ThreeFold_filter].set(vec_z_threeFold)
        
        
        # up to this point, z-axis should already be set up and normalized
        xz_projection = jnp.sum(vec_x*vec_z, axis = 1, keepdims=True)
        vec_x = normalize(vec_x - vec_z * xz_projection, axis=1)
        # up to this point, x-axis should be ready
        vec_y = jnp.cross(vec_z, vec_x)

        return jnp.stack((vec_x, vec_y, vec_z), axis=1)
    return c_l_f

def rot_global2local(Q_gh, localframes, lmax = 2):
    '''
    This one rotate harmonic moments Q from global frame to local frame

    Input:
        Q_gh: 
            n * (l+1)^2, stores the global harmonic multipole moments of each site
        localframes: 
            n * 3 * 3, stores the Rotation matrix for each site, the R is defined as:
            [r1, r2, r3]^T, with r1, r2, r3 being the local frame axes
        lmax:
            integer, the maximum multipole order

    Output:
        Qrot:
            n * (l+1)^2, stores the rotated multipole moments
    '''
    if lmax > 2:
        raise NotImplementedError('l > 2 (beyond quadrupole) not supported')

    # monopole
    Q_lh_0 = Q_gh[:, 0]
    # for dipole
    if lmax >= 1:
        zxy = np.array([2,0,1])
        # the rotation matrix
        R1 = localframes[:, zxy][:,:,zxy]
        # rotate
        Q_lh_1 = np.sum(R1*Q_gh[:,np.newaxis,1:4], axis = 2)
    if lmax >= 2:
        rt3 = np.sqrt(3)
        xx = localframes[:, 0, 0]
        xy = localframes[:, 0, 1]
        xz = localframes[:, 0, 2]
        yx = localframes[:, 1, 0]
        yy = localframes[:, 1, 1]
        yz = localframes[:, 1, 2]
        zx = localframes[:, 2, 0]
        zy = localframes[:, 2, 1]
        zz = localframes[:, 2, 2]
        quadrupoles = Q_gh[:, 4:9]
        # construct the local->global transformation matrix
        # this is copied directly from the convert_mom_to_xml.py code
        C2_gl_00 = (3*zz**2-1)/2
        C2_gl_01 = rt3*zx*zz
        C2_gl_02 = rt3*zy*zz
        C2_gl_03 = (rt3*(-2*zy**2-zz**2+1))/2
        C2_gl_04 = rt3*zx*zy
        C2_gl_10 = rt3*xz*zz
        C2_gl_11 = 2*xx*zz-yy
        C2_gl_12 = yx+2*xy*zz
        C2_gl_13 = -2*xy*zy-xz*zz
        C2_gl_14 = xx*zy+zx*xy
        C2_gl_20 = rt3*yz*zz
        C2_gl_21 = 2*yx*zz+xy
        C2_gl_22 = -xx+2*yy*zz
        C2_gl_23 = -2*yy*zy-yz*zz
        C2_gl_24 = yx*zy+zx*yy
        C2_gl_30 = rt3*(-2*yz**2-zz**2+1)/2
        C2_gl_31 = -2*yx*yz-zx*zz
        C2_gl_32 = -2*yy*yz-zy*zz
        C2_gl_33 = (4*yy**2+2*zy**2+2*yz**2+zz**2-3)/2
        C2_gl_34 = -2*yx*yy-zx*zy
        C2_gl_40 = rt3*xz*yz
        C2_gl_41 = xx*yz+yx*xz
        C2_gl_42 = xy*yz+yy*xz
        C2_gl_43 = -2*xy*yy-xz*yz
        C2_gl_44 = xx*yy+yx*xy
        # rotate
        C2_gl = jnp.array(
            [
                [C2_gl_00, C2_gl_10, C2_gl_20, C2_gl_30, C2_gl_40],
                [C2_gl_01, C2_gl_11, C2_gl_21, C2_gl_31, C2_gl_41],
                [C2_gl_02, C2_gl_12, C2_gl_22, C2_gl_32, C2_gl_42],
                [C2_gl_03, C2_gl_13, C2_gl_23, C2_gl_33, C2_gl_43],
                [C2_gl_04, C2_gl_14, C2_gl_24, C2_gl_34, C2_gl_44]
            ]
        ).swapaxes(0,2)
        Q_lh_2 = jnp.einsum('ijk,ik->ij', C2_gl, quadrupoles)
    Q_lh = jnp.hstack([Q_lh_0[:,np.newaxis], Q_lh_1, Q_lh_2])

    return Q_lh

def rot_local2global(Q_lh, localframes, lmax = 2):
    return rot_global2local(Q_lh, jnp.swapaxes(localframes, 1, 2), lmax)

def calc_ePermCoef(mscales, kappa, dr):

    '''
    This function calculates the ePermCoefs at once

    Inputs:
        mscales:
            array: same as pme_realspace()
        kappa:
            float: same as pme_realspace()
        dr: 
            float: distance between one pair of particles
    Output:
        cc, cd, dd_m0, dd_m1, cq, dq_m0, dq_m1, qq_m0, qq_m1, qq_m2:
            n * 1 array: ePermCoefs
    '''

    # be aware of unit and dimension !!

    prefactor = dielectric
    
    rInvVec = jnp.array([prefactor/dr**i for i in range(0, 9)])
    
    alphaRVec = jnp.array([(kappa*dr)**i for i in range(0, 10)])
    
    X = 2 * jnp.exp( -alphaRVec[2] ) / jnp.sqrt(np.pi)
    tmp = jnp.array(alphaRVec[1])
    doubleFactorial = 1
    facCount = 1
    erfAlphaR = erf(alphaRVec[1])
    bVec = jnp.empty((6, len(erfAlphaR)))
    bVec = bVec.at[1].set(-erfAlphaR)
    for i in range(2, 6):
        bVec = bVec.at[i].set(bVec[i-1]+tmp*X/doubleFactorial)
        facCount += 2
        doubleFactorial *= facCount
        tmp *= 2 * alphaRVec[2]
    
    
    # C-C: 1
    
    cc = rInvVec[1] * (mscales + bVec[2] - alphaRVec[1]*X) 
    
    # C-D: 1
    
    cd = rInvVec[2] * ( mscales + bVec[2] )
    
    ## D-D: 2
    
    dd_m0 = -2/3 * rInvVec[3] * (3*(mscales + bVec[3]) + alphaRVec[3]*X )
    
    dd_m1 = rInvVec[3] * (mscales + bVec[3] - (2/3)*alphaRVec[3]*X)
    
    ## C-Q: 1
    
    cq = (mscales + bVec[3])*rInvVec[3]
    
    ## D-Q: 2
    
    dq_m0 = rInvVec[4] * (3* (mscales + bVec[3])+ (4/3) * alphaRVec[5]*X)
    dq_m1 = -jnp.sqrt(3)*rInvVec[4]*(mscales+bVec[3])
    
    ## Q-Q
    
    qq_m0 = rInvVec[5] * (6* (mscales + bVec[4])+ (4/45)* (-3 + 10*alphaRVec[2]) * alphaRVec[5]*X)
    qq_m1 = -(4/15)*rInvVec[5]*(15*(mscales+bVec[4]) + alphaRVec[5]*X)
    qq_m2 = rInvVec[5]*(mscales + bVec[4] - (4/15)*alphaRVec[5]*X)

    return cc, cd, dd_m0, dd_m1, cq, dq_m0, dq_m1, qq_m0, qq_m1, qq_m2

def pme_real(positions, box, rc, Qlocal, construct_localframes, kappa, covalent_map, mScales, pScales, dScales):
    '''
    This function computes the realspace part of PME interactions.

    Inputs:
        positions:
            N * 3, positions of all atoms, in ANGSTROM
        box:
            3 * 3, box size, axes arranged in rows, in ANGSTROM
        rc: 
            float, radius of cutoff, in ANGSTROM
        Qlocal:
            N * (lmax+1)^2, fixed multipole moments in local spherical harmonics
        construct_localframes:
            function: to construct localframes
                Iputs:
                    positions, box
        kappa:
            float, the short range attenuation in PME, in ANGSTROM^-1
        covalent_map:
            N * N, in ndarray, each row specifies the covalent neighbors of each atom
            0 means not covalent neighor, 1 means one bond away, 2 means two bonds away etc.
        mScales:
            len(n) vector, the fixed multipole - fixed multipole interaction damping coefficients, (n) is the
            maximum exclusion distance
        pScales:
            len(n) vector, the fixed multipole - induced dipole scalings
        dScales:
            len(n) vector, the induced - induced dipole scalings

    Outputs:
        ene_real: 
            float, real space energy
    '''
    
    # The following is written by Roy Kid
    # any confusion please email lijichen365@126.com directly 
    
    nbs_obj = construct_nblist(positions, box, rc)
    local_frames = construct_localframes(positions, box)
    
    Qglobal = rot_local2global(Qlocal, local_frames, 2)

    mScales = jnp.concatenate([mScales, jnp.array([1])])
    pScales = jnp.concatenate([pScales, jnp.array([1])])
    dScales = jnp.concatenate([dScales, jnp.array([1])])

    pairs = jnp.array(nbs_obj.nbs)
    distances2 = jnp.array(nbs_obj.distances2)
    distances = jnp.sqrt(distances2)
    dr_vecs = nbs_obj.dr_vecs
    Ri = jnp.array(nbs_obj.R)
    
    nbonds = covalent_map[pairs[:, 0], pairs[:, 1]]
    mscales = mScales[nbonds-1]
    
    # construct rotation matrix

    Q_extendi = Qglobal[pairs[:, 0]]
    Q_extendj = Qglobal[pairs[:, 1]]

    qiQI = rot_global2local(Q_extendi, Ri, 2)
    qiQJ = rot_global2local(Q_extendj, Ri, 2)

    cc, cd, dd_m0, dd_m1, cq, dq_m0, dq_m1, qq_m0, qq_m1, qq_m2 = calc_ePermCoef(mscales, kappa, distances)
    

    Vij0 = cc*qiQJ[:, 0]
    Vji0 = cc*qiQI[:, 0]

    # C-D 
    
    Vij0 = Vij0 - cd*qiQJ[:, 1]
    Vji1 = -cd*qiQI[:, 0]
    Vij1 = cd*qiQJ[:, 0]
    Vji0 = Vji0 + cd*qiQI[:, 1]

    # D-D m0 
    
    Vij1 += dd_m0 * qiQJ[:, 1]
    Vji1 += dd_m0 * qiQI[:, 1]
    
    # D-D m1 
    
    Vij2 = dd_m1*qiQJ[:, 2]
    Vji2 = dd_m1*qiQI[:, 2]
    Vij3 = dd_m1*qiQJ[:, 3]
    Vji3 = dd_m1*qiQI[:, 3]

    # C-Q
    
    Vij0 = Vij0 + cq*qiQJ[:, 4]
    Vji4 = cq*qiQI[:, 0]
    Vij4 = cq*qiQJ[:, 0]
    Vji0 = Vji0 + cq*qiQI[:, 4]
    
    # D-Q m0
    
    Vij1 += dq_m0*qiQJ[:, 4]
    Vji4 += dq_m0*qiQI[:, 1] 
    
    # Q-D m0
    
    Vij4 -= dq_m0*qiQJ[:, 1]
    Vji1 -= dq_m0*qiQI[:, 4]
    
    # D-Q m1
    
    Vij2 = Vij2 + dq_m1*qiQJ[:, 5]
    Vji5 = dq_m1*qiQI[:, 2]

    Vij3 += dq_m1*qiQJ[:, 6]
    Vji6 = dq_m1*qiQI[:, 3]
    
    Vij5 = -(dq_m1*qiQJ[:, 2])
    Vji2 += -(dq_m1*qiQI[:, 5])
    
    Vij6 = -(dq_m1*qiQJ[:, 3])
    Vji3 += -(dq_m1*qiQI[:, 6])
    
    # Q-Q m0
    
    Vij4 += qq_m0*qiQJ[:, 4]
    Vji4 += qq_m0*qiQI[:, 4] 
    
    # Q-Q m1
    
    Vij5 += qq_m1*qiQJ[:, 5]
    Vji5 += qq_m1*qiQI[:, 5]
    Vij6 += qq_m1*qiQJ[:, 6]
    Vji6 += qq_m1*qiQI[:, 6]
    
    # Q-Q m2
    
    Vij7  = qq_m2*qiQJ[:, 7]
    Vji7  = qq_m2*qiQI[:, 7]
    Vij8  = qq_m2*qiQJ[:, 8]
    Vji8  = qq_m2*qiQI[:, 8]
    
    Vij = jnp.vstack((Vij0, Vij1, Vij2, Vij3, Vij4, Vij5, Vij6, Vij7, Vij8))
    Vji = jnp.vstack((Vji0, Vji1, Vji2, Vji3, Vji4, Vji5, Vji6, Vji7, Vji8))

    
    return 0.5*(jnp.sum(qiQI*Vij.T)+jnp.sum(qiQJ*Vji.T))

def pme_self(Q_h, kappa, lmax = 2):
    '''
    This function calculates the PME self energy

    Inputs:
        Q:
            N * (lmax+1)^2: harmonic multipoles, local or global does not matter
        kappa:
            float: kappa used in PME

    Output:
        ene_self:
            float: the self energy
    '''
    n_harms = (lmax + 1) ** 2    
    l_list = np.array([0]+[1,]*3+[2,]*5)[:n_harms]
    l_fac2 = np.array([1]+[3,]*3+[15,]*5)[:n_harms]
    factor = kappa/np.sqrt(np.pi) * (2*kappa**2)**l_list / l_fac2
    return - jnp.sum(factor[np.newaxis] * Q_h**2) * dielectric

def gen_pme_reciprocal(axis_type, axis_indices):
    construct_localframes = generate_construct_localframes(axis_type, axis_indices)
    def pme_reciprocal_on_Qgh(positions, box, Q, kappa, lmax, K1, K2, K3):
        '''
        This function calculates the PME reciprocal space energy

        Inputs:
            positions:
                N_a x 3 atomic positions
            box:
                3 x 3 box vectors in angstrom
            Q:
                N_a x (lmax + 1)**2 multipole values in global frame
            kappa:
                characteristic reciprocal length scale
            lmax:
                maximum value of the multipole level
            K1, K2, K3:
                int: the dimensions of the mesh grid

        Output:
            ene_reciprocal:
                float: the reciprocal space energy
        '''
        N = np.array([K1,K2,K3])
        ################
        bspline_range = jnp.arange(-3, 3)
        shifts = jnp.array(jnp.meshgrid(bspline_range, bspline_range, bspline_range)).T.reshape((1, 216, 3))
        
        def get_recip_vectors(N, box):
            """
            Computes reciprocal lattice vectors of the grid
            
            Input:
                N:
                    (3,)-shaped array
                box:
                    3 x 3 matrix, box parallelepiped vectors arranged in TODO rows or columns?
                    
            Output: 
                Nj_Aji_star:
                    3 x 3 matrix, the first index denotes reciprocal lattice vector, the second index is the component xyz.
                    (lattice vectors arranged in rows)
            """
            Nj_Aji_star = (N.reshape((1, 3)) * jnp.linalg.inv(box)).T
            return Nj_Aji_star

        def u_reference(R_a, Nj_Aji_star):
            """
            Each atom is meshed to PME_ORDER**3 points on the m-meshgrid. This function computes the xyz-index of the reference point, which is the point on the meshgrid just above atomic coordinates, and the corresponding values of xyz fractional displacements from real coordinate to the reference point. 
            
            Inputs:
                R_a:
                    N_a * 3 matrix containing positions of sites
                Nj_Aji_star:
                    3 x 3 matrix, the first index denotes reciprocal lattice vector, the second index is the component xyz.
                    (lattice vectors arranged in rows)
                    
            Outputs:
                m_u0: 
                    N_a * 3 matrix, positions of the reference points of R_a on the m-meshgrid
                u0: 
                    N_a * 3 matrix, (R_a - R_m)*a_star values
            """
            
            R_in_m_basis =  jnp.einsum("ij,kj->ki", Nj_Aji_star, R_a)
            
            m_u0 = jnp.ceil(R_in_m_basis).astype(int)
            
            u0 = (m_u0 - R_in_m_basis) + 6/2
            return m_u0, u0

        def bspline6(u):
            """
            Computes the cardinal B-spline function
            """
            return jnp.piecewise(u, 
                                [jnp.logical_and(u>=0, u<1.), 
                                jnp.logical_and(u>=1, u<2.), 
                                jnp.logical_and(u>=2, u<3.), 
                                jnp.logical_and(u>=3, u<4.), 
                                jnp.logical_and(u>=4, u<5.), 
                                jnp.logical_and(u>=5, u<6.)],
                                [lambda u: u**5/120,
                                lambda u: u**5/120 - (u - 1)**5/20,
                                lambda u: u**5/120 + (u - 2)**5/8 - (u - 1)**5/20,
                                lambda u: u**5/120 - (u - 3)**5/6 + (u - 2)**5/8 - (u - 1)**5/20,
                                lambda u: u**5/24 - u**4 + 19*u**3/2 - 89*u**2/2 + 409*u/4 - 1829/20,
                                lambda u: -u**5/120 + u**4/4 - 3*u**3 + 18*u**2 - 54*u + 324/5] )

        def bspline6prime(u):
            """
            Computes first derivative of the cardinal B-spline function
            """
            return jnp.piecewise(u, 
                                [jnp.logical_and(u>=0., u<1.), 
                                jnp.logical_and(u>=1., u<2.), 
                                jnp.logical_and(u>=2., u<3.), 
                                jnp.logical_and(u>=3., u<4.), 
                                jnp.logical_and(u>=4., u<5.), 
                                jnp.logical_and(u>=5., u<6.)],
                                [lambda u: u**4/24,
                                lambda u: u**4/24 - (u - 1)**4/4,
                                lambda u: u**4/24 + 5*(u - 2)**4/8 - (u - 1)**4/4,
                                lambda u: -5*u**4/12 + 6*u**3 - 63*u**2/2 + 71*u - 231/4,
                                lambda u: 5*u**4/24 - 4*u**3 + 57*u**2/2 - 89*u + 409/4,
                                lambda u: -u**4/24 + u**3 - 9*u**2 + 36*u - 54] )

        def bspline6prime2(u):
            """
            Computes second derivate of the cardinal B-spline function
            """
            return jnp.piecewise(u, 
                                [jnp.logical_and(u>=0., u<1.), 
                                jnp.logical_and(u>=1., u<2.), 
                                jnp.logical_and(u>=2., u<3.), 
                                jnp.logical_and(u>=3., u<4.), 
                                jnp.logical_and(u>=4., u<5.), 
                                jnp.logical_and(u>=5., u<6.)],
                                [lambda u: u**3/6,
                                lambda u: u**3/6 - (u - 1)**3,
                                lambda u: 5*u**3/3 - 12*u**2 + 27*u - 19,
                                lambda u: -5*u**3/3 + 18*u**2 - 63*u + 71,
                                lambda u: 5*u**3/6 - 12*u**2 + 57*u - 89,
                                lambda u: -u**3/6 + 3*u**2 - 18*u + 36,] )


        def theta_eval(u, M_u):
            """
            Evaluates the value of theta given 3D u values at ... points 
            
            Input:
                u:
                    ... x 3 matrix

            Output:
                theta:
                    ... matrix
            """
            theta = jnp.prod(M_u, axis = -1)
            return theta

        def thetaprime_eval(u, Nj_Aji_star, M_u, Mprime_u):
            """
            First derivative of theta with respect to x,y,z directions
            
            Input:
                u
                Nj_Aji_star:
                    reciprocal lattice vectors
            
            Output:
                N_a * 3 matrix
            """

            div = jnp.array([
                Mprime_u[:, 0] * M_u[:, 1] * M_u[:, 2],
                Mprime_u[:, 1] * M_u[:, 2] * M_u[:, 0],
                Mprime_u[:, 2] * M_u[:, 0] * M_u[:, 1],
            ]).T
            
            # Notice that u = m_u0 - R_in_m_basis + 6/2
            # therefore the Jacobian du_j/dx_i = - Nj_Aji_star
            return jnp.einsum("ij,kj->ki", -Nj_Aji_star, div)

        def theta2prime_eval(u, Nj_Aji_star, M_u, Mprime_u, M2prime_u):
            """
            compute the 3 x 3 second derivatives of theta with respect to xyz
            
            Input:
                u
                Nj_Aji_star
            
            Output:
                N_A * 3 * 3
            """

            div_00 = M2prime_u[:, 0] * M_u[:, 1] * M_u[:, 2]
            div_11 = M2prime_u[:, 1] * M_u[:, 0] * M_u[:, 2]
            div_22 = M2prime_u[:, 2] * M_u[:, 0] * M_u[:, 1]
            
            div_01 = Mprime_u[:, 0] * Mprime_u[:, 1] * M_u[:, 2]
            div_02 = Mprime_u[:, 0] * Mprime_u[:, 2] * M_u[:, 1]
            div_12 = Mprime_u[:, 1] * Mprime_u[:, 2] * M_u[:, 0]

            div_10 = div_01
            div_20 = div_02
            div_21 = div_12
            
            div = jnp.array([
                [div_00, div_01, div_02],
                [div_10, div_11, div_12],
                [div_20, div_21, div_22],
            ]).swapaxes(0, 2)
            
            # Notice that u = m_u0 - R_in_m_basis + 6/2
            # therefore the Jacobian du_j/dx_i = - Nj_Aji_star
            return jnp.einsum("im,jn,kmn->kij", -Nj_Aji_star, -Nj_Aji_star, div)

        def sph_harmonics_GO(u0, Nj_Aji_star):
            '''
            Find out the value of spherical harmonics GRADIENT OPERATORS, assume the order is:
            00, 10, 11c, 11s, 20, 21c, 21s, 22c, 22s, ...
            Currently supports lmax <= 2

            Inputs:
                u0: 
                    a N_a * 3 matrix containing all positions
                Nj_Aji_star:
                    reciprocal lattice vectors in the m-grid

            Output: 
                harmonics: 
                    a Na * (6**3) * (l+1)^2 matrix, STGO operated on theta,
                    evaluated at 6*6*6 integer points about reference points m_u0 
            '''
            
            n_harm = (lmax + 1)**2

            N_a = u0.shape[0]
            u = (u0[:, jnp.newaxis, :] + shifts).reshape((N_a*216, 3)) 

            M_u = bspline6(u)
            theta = theta_eval(u, M_u)
            if lmax == 0:
                return theta.reshape(N_a, 216, n_harm)
            
            # dipole
            Mprime_u = bspline6prime(u)
            thetaprime = thetaprime_eval(u, Nj_Aji_star, M_u, Mprime_u)
            harmonics_1 = jnp.stack(
                [theta,
                thetaprime[:, 2],
                thetaprime[:, 0],
                thetaprime[:, 1]],
                axis = -1
            )
            
            if lmax == 1:
                return harmonics_1.reshape(N_a, 216, n_harm)

            # quadrapole
            M2prime_u = bspline6prime2(u)
            theta2prime = theta2prime_eval(u, Nj_Aji_star, M_u, Mprime_u, M2prime_u)
            rt3 = jnp.sqrt(3)
            harmonics_2 = jnp.hstack(
                [harmonics_1,
                jnp.stack([(3*theta2prime[:,2,2] - jnp.trace(theta2prime, axis1=1, axis2=2)) / 2,
                rt3 * theta2prime[:, 0, 2],
                rt3 * theta2prime[:, 1, 2],
                rt3/2 * (theta2prime[:, 0, 0] - theta2prime[:, 1, 1]),
                rt3 * theta2prime[:, 0, 1]], axis = 1)]
            )
            if lmax == 2:
                return harmonics_2.reshape(N_a, 216, n_harm)
            else:
                raise NotImplementedError('l > 2 (beyond quadrupole) not supported')
            
        def Q_m_peratom(Q, sph_harms):
            """
            Computes <R_t|Q>. See eq. (49) of https://doi.org/10.1021/ct5007983
            
            Inputs:
                Q: 
                    N_a * (l+1)**2 matrix containing global frame multipole moments up to lmax,
                sph_harms:
                    N_a, 216, (l+1)**2
            
            Output:
                Q_m_pera:
                    N_a * 216 matrix, values of theta evaluated on a 6 * 6 block about the atoms
            """
            
            N_a = sph_harms.shape[0]
            
            if lmax > 2:
                raise NotImplementedError('l > 2 (beyond quadrupole) not supported')

            Q_dbf = Q[:, 0]
            if lmax >= 1:
                Q_dbf = jnp.hstack([Q_dbf[:,jnp.newaxis], Q[:,1:4]])
            if lmax >= 2:
                Q_dbf = jnp.hstack([Q_dbf, Q[:,4:9]/3])
            
            Q_m_pera = jnp.sum( Q_dbf[:,jnp.newaxis,:]* sph_harms, axis=2)

            assert Q_m_pera.shape == (N_a, 216)
            return Q_m_pera
        
        def Q_mesh_on_m(Q_mesh_pera, m_u0, N):
            """
            spreads the particle mesh onto the grid
            
            Input:
                Q_mesh_pera, m_u0, N
                
            Output:
                Q_mesh: 
                    Nx * Ny * Nz matrix
            """


            indices_arr = jnp.mod(m_u0[:,np.newaxis,:]+shifts, N[np.newaxis, np.newaxis, :])
            
            ### jax trick implementation without using for loop
            ### NOTICE: this implementation does not work with numpy!
            Q_mesh = jnp.zeros((N[0], N[1], N[2]))
            Q_mesh = Q_mesh.at[indices_arr[:, :, 0], indices_arr[:, :, 1], indices_arr[:, :, 2]].add(Q_mesh_pera)
            
            return Q_mesh

        def setup_kpts_integer(N):
            """
            Outputs:
                kpts_int:
                    n_k * 3 matrix, n_k = N[0] * N[1] * N[2]
            """
            N_half = N.reshape(3)

            kx, ky, kz = [jnp.roll(jnp.arange(- (N_half[i] - 1) // 2, (N_half[i] + 1) // 2 ), - (N_half[i] - 1) // 2) for i in range(3)]

            kpts_int = jnp.hstack([ki.flatten()[:,jnp.newaxis] for ki in jnp.meshgrid(kz, kx, ky)])

            return kpts_int 

        def setup_kpts(box, kpts_int):
            '''
            This function sets up the k-points used for reciprocal space calculations
            
            Input:
                box:
                    3 * 3, three axis arranged in rows
                kpts_int:
                    n_k * 3 matrix

            Output:
                kpts:
                    4 * K, K=K1*K2*K3, contains kx, ky, kz, k^2 for each kpoint
            '''
            # in this array, a*, b*, c* (without 2*pi) are arranged in column
            box_inv = jnp.linalg.inv(box)

            # K * 3, coordinate in reciprocal space
            kpts = 2 * jnp.pi * kpts_int.dot(box_inv)

            ksr = jnp.sum(kpts**2, axis=1)

            # 4 * K
            kpts = jnp.hstack((kpts, ksr[:, jnp.newaxis])).T

            return kpts

        def E_recip_on_grid(Q_mesh, box, N, kappa):
            """
            Computes the reciprocal part energy
            """
            
            N = N.reshape(1,1,3)
            kpts_int = setup_kpts_integer(N)
            kpts = setup_kpts(box, kpts_int)

            m = jnp.linspace(-2,2,5).reshape(5, 1, 1)
            # theta_k : array of shape n_k
            theta_k = jnp.prod(
                jnp.sum(
                    bspline6(m + 6/2) * jnp.cos(2*jnp.pi*m*kpts_int[jnp.newaxis] / N),
                    axis = 0
                ),
                axis = 1
            )

            S_k = jnp.fft.fftn(Q_mesh)

            S_k = S_k.flatten()

            E_k = 2*jnp.pi/kpts[3,1:]/jnp.linalg.det(box) * jnp.exp( - kpts[3, 1:] /4 /kappa**2) * jnp.abs(S_k[1:]/theta_k[1:])**2
            return jnp.sum(E_k)
        
        Nj_Aji_star = get_recip_vectors(N, box)
        m_u0, u0    = u_reference(positions, Nj_Aji_star)
        sph_harms   = sph_harmonics_GO(u0, Nj_Aji_star)
        Q_mesh_pera = Q_m_peratom(Q, sph_harms)
        Q_mesh      = Q_mesh_on_m(Q_mesh_pera, m_u0, N)
        E_recip     = E_recip_on_grid(Q_mesh, box, N, kappa)
        
        # Outputs energy in OPENMM units
        return E_recip*dielectric

    def pme_reciprocal(positions, box,  Q_lh, kappa, lmax, K1, K2, K3):
        localframes = construct_localframes(positions, box)
        Q_gh = rot_local2global(Q_lh, localframes, lmax)
        return pme_reciprocal_on_Qgh(positions, box, Q_gh, kappa, lmax, K1, K2, K3)
    return pme_reciprocal