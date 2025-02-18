"""
Locally Optimal Block Preconditioned Conjugate Gradient Method (LOBPCG).

References
----------
.. [1] A. V. Knyazev (2001),
       Toward the Optimal Preconditioned Eigensolver: Locally Optimal
       Block Preconditioned Conjugate Gradient Method.
       SIAM Journal on Scientific Computing 23, no. 2,
       pp. 517-541. :doi:`10.1137/S1064827500366124`

.. [2] A. V. Knyazev, I. Lashuk, M. E. Argentati, and E. Ovchinnikov (2007),
       Block Locally Optimal Preconditioned Eigenvalue Xolvers (BLOPEX)
       in hypre and PETSc.  :arxiv:`0705.2626`

.. [3] A. V. Knyazev's C and MATLAB implementations:
       https://github.com/lobpcg/blopex
"""

import warnings
import numpy as np
from scipy.linalg import (inv, eigh, cho_factor, cho_solve,
                          cholesky, LinAlgError)
from scipy.sparse.linalg import LinearOperator, aslinearoperator
from scipy.sparse import isspmatrix
from numpy import block as bmat

__all__ = ["lobpcg"]


def _report_nonhermitian(M, name):
    """
    Report if `M` is not a Hermitian matrix given its type.
    """
    from scipy.linalg import norm

    md = M - M.T.conj()
    nmd = norm(md, 1)
    tol = 10 * np.finfo(M.dtype).eps
    tol = max(tol, tol * norm(M, 1))
    if nmd > tol:
        warnings.warn(
              f"Matrix {name} of the type {M.dtype} is not Hermitian: "
              f"condition: {nmd} < {tol} fails.",
              UserWarning, stacklevel=4
         )

def _as2d(ar):
    """
    If the input array is 2D return it, if it is 1D, append a dimension,
    making it a column vector.
    """
    if ar.ndim == 2:
        return ar
    else:  # Assume 1!
        aux = np.array(ar, copy=False)
        aux.shape = (ar.shape[0], 1)
        return aux


def _makeOperator(operatorInput, expectedShape):
    """Takes a dense numpy array or a sparse matrix or
    a function and makes an operator performing matrix * blockvector
    products."""
    if operatorInput is None:
        return None
    else:
        operator = aslinearoperator(operatorInput)

    if operator.shape != expectedShape:
        raise ValueError("operator has invalid shape")

    return operator


def _applyConstraints(blockVectorV, factYBY, blockVectorBY, blockVectorY):
    """Changes blockVectorV in place."""
    YBV = np.dot(blockVectorBY.T.conj(), blockVectorV)
    tmp = cho_solve(factYBY, YBV)
    blockVectorV -= np.dot(blockVectorY, tmp)


def _b_orthonormalize(B, blockVectorV, blockVectorBV=None, retInvR=False):
    """B-orthonormalize the given block vector using Cholesky."""
    normalization = blockVectorV.max(axis=0) + np.finfo(blockVectorV.dtype).eps
    blockVectorV = blockVectorV / normalization
    if blockVectorBV is None:
        if B is not None:
            blockVectorBV = B(blockVectorV)
        else:
            blockVectorBV = blockVectorV  # Shared data!!!
    else:
        blockVectorBV = blockVectorBV / normalization
    VBV = blockVectorV.T.conj() @ blockVectorBV
    try:
        # VBV is a Cholesky factor from now on...
        VBV = cholesky(VBV, overwrite_a=True)
        VBV = inv(VBV, overwrite_a=True)
        blockVectorV = blockVectorV @ VBV
        # blockVectorV = (cho_solve((VBV.T, True), blockVectorV.T)).T
        if B is not None:
            blockVectorBV = blockVectorBV @ VBV
            # blockVectorBV = (cho_solve((VBV.T, True), blockVectorBV.T)).T
        else:
            blockVectorBV = None
    except LinAlgError:
        # raise ValueError('Cholesky has failed')
        blockVectorV = None
        blockVectorBV = None
        VBV = None

    if retInvR:
        return blockVectorV, blockVectorBV, VBV, normalization
    else:
        return blockVectorV, blockVectorBV


def _get_indx(_lambda, num, largest):
    """Get `num` indices into `_lambda` depending on `largest` option."""
    ii = np.argsort(_lambda)
    if largest:
        ii = ii[:-num - 1:-1]
    else:
        ii = ii[:num]

    return ii


def lobpcg(
    A,
    X,
    B=None,
    M=None,
    Y=None,
    tol=None,
    maxiter=None,
    largest=True,
    verbosityLevel=0,
    retLambdaHistory=False,
    retResidualNormsHistory=False,
):
    """Locally Optimal Block Preconditioned Conjugate Gradient Method (LOBPCG).

    LOBPCG is a preconditioned eigensolver for large symmetric positive
    definite (SPD) generalized eigenproblems.

    Parameters
    ----------
    A : {sparse matrix, dense matrix, LinearOperator}
        The symmetric linear operator of the problem, usually a
        sparse matrix.  Often called the "stiffness matrix".
    X : ndarray, float32 or float64
        Initial approximation to the ``k`` eigenvectors (non-sparse). If `A`
        has ``shape=(n,n)`` then `X` should have shape ``shape=(n,k)``.
    B : {dense matrix, sparse matrix, LinearOperator}, optional
        The right hand side operator in a generalized eigenproblem.
        By default, ``B = Identity``.  Often called the "mass matrix".
    M : {dense matrix, sparse matrix, LinearOperator}, optional
        Preconditioner to `A`; by default ``M = Identity``.
        `M` should approximate the inverse of `A`.
    Y : ndarray, float32 or float64, optional
        An n-by-sizeY matrix of constraints (non-sparse), sizeY < n.
        The iterations will be performed in the B-orthogonal complement
        of the column-space of Y. Y must be full rank.
    tol : scalar, optional
        Solver tolerance (stopping criterion).
        The default is ``tol=n*sqrt(eps)``.
    maxiter : int, optional
        Maximum number of iterations.  The default is ``maxiter = 20``.
    largest : bool, optional
        When True, solve for the largest eigenvalues, otherwise the smallest.
    verbosityLevel : int, optional
        Controls solver output.  The default is ``verbosityLevel=0``.
    retLambdaHistory : bool, optional
        Whether to return eigenvalue history.  Default is False.
    retResidualNormsHistory : bool, optional
        Whether to return history of residual norms.  Default is False.

    Returns
    -------
    w : ndarray
        Array of ``k`` eigenvalues.
    v : ndarray
        An array of ``k`` eigenvectors.  `v` has the same shape as `X`.
    lambdas : list of ndarray, optional
        The eigenvalue history, if `retLambdaHistory` is True.
    rnorms : list of ndarray, optional
        The history of residual norms, if `retResidualNormsHistory` is True.

    Notes
    -----
    If both ``retLambdaHistory`` and ``retResidualNormsHistory`` are True,
    the return tuple has the following format
    ``(lambda, V, lambda history, residual norms history)``.

    In the following ``n`` denotes the matrix size and ``k`` the number
    of required eigenvalues (smallest or largest).

    The LOBPCG code internally solves eigenproblems of the size ``3k`` on every
    iteration by calling the "standard" dense eigensolver, so if ``k`` is not
    small enough compared to ``n``, it does not make sense to call the LOBPCG
    code, but rather one should use the "standard" eigensolver, e.g. numpy or
    scipy function in this case.
    If one calls the LOBPCG algorithm for ``5k > n``, it will most likely break
    internally, so the code tries to call the standard function instead.

    It is not that ``n`` should be large for the LOBPCG to work, but rather the
    ratio ``n / k`` should be large. It you call LOBPCG with ``k=1``
    and ``n=10``, it works though ``n`` is small. The method is intended
    for extremely large ``n / k``.

    The convergence speed depends basically on two factors:

    1. Relative separation of the seeking eigenvalues from the rest
       of the eigenvalues. One can vary ``k`` to improve the absolute
       separation and use proper preconditioning to shrink the spectral spread.
       For example, a rod vibration test problem (under tests
       directory) is ill-conditioned for large ``n``, so convergence will be
       slow, unless efficient preconditioning is used. For this specific
       problem, a good simple preconditioner function would be a linear solve
       for `A`, which is easy to code since `A` is tridiagonal.

    2. Quality of the initial approximations `X` to the seeking eigenvectors.
       Randomly distributed around the origin vectors work well if no better
       choice is known.

    References
    ----------
    .. [1] A. V. Knyazev (2001),
           Toward the Optimal Preconditioned Eigensolver: Locally Optimal
           Block Preconditioned Conjugate Gradient Method.
           SIAM Journal on Scientific Computing 23, no. 2,
           pp. 517-541. :doi:`10.1137/S1064827500366124`

    .. [2] A. V. Knyazev, I. Lashuk, M. E. Argentati, and E. Ovchinnikov
           (2007), Block Locally Optimal Preconditioned Eigenvalue Xolvers
           (BLOPEX) in hypre and PETSc. :arxiv:`0705.2626`

    .. [3] A. V. Knyazev's C and MATLAB implementations:
           https://github.com/lobpcg/blopex

    Examples
    --------
    Solve ``A x = lambda x`` with constraints and preconditioning.

    >>> import numpy as np
    >>> from scipy.sparse import spdiags, issparse
    >>> from scipy.sparse.linalg import lobpcg, LinearOperator

    The square matrix size:

    >>> n = 100
    >>> vals = np.arange(1, n + 1)

    The first mandatory input parameter, in this test
    a sparse 2D array representing the square matrix
    of the eigenvalue problem to solve:

    >>> A = spdiags(vals, 0, n, n)
    >>> A.toarray()
    array([[  1.,   0.,   0., ...,   0.,   0.,   0.],
           [  0.,   2.,   0., ...,   0.,   0.,   0.],
           [  0.,   0.,   3., ...,   0.,   0.,   0.],
           ...,
           [  0.,   0.,   0., ...,  98.,   0.,   0.],
           [  0.,   0.,   0., ...,   0.,  99.,   0.],
           [  0.,   0.,   0., ...,   0.,   0., 100.]])

    Initial guess for eigenvectors, should have linearly independent
    columns. The second mandatory input parameter, a 2D array with the
    row dimension determining the number of requested eigenvalues.
    If no initial approximations available, randomly oriented vectors
    commonly work best, e.g., with components normally disrtibuted
    around zero or uniformly distributed on the interval [-1 1].

    >>> rng = np.random.default_rng()
    >>> X = rng.normal(size=(n, 3))

    Constraints - an optional input parameter is a 2D array comprising
    of column vectors that the eigenvectors must be orthogonal to:

    >>> Y = np.eye(n, 3)

    Preconditioner in the inverse of A in this example:

    >>> invA = spdiags([1./vals], 0, n, n)

    The preconditiner must be defined by a function:

    >>> def precond( x ):
    ...     return invA @ x

    The argument x of the preconditioner function is a matrix inside `lobpcg`,
    thus the use of matrix-matrix product ``@``.

    The preconditioner function is passed to lobpcg as a `LinearOperator`:

    >>> M = LinearOperator(matvec=precond, matmat=precond,
    ...                    shape=(n, n), dtype=np.float64)

    Let us now solve the eigenvalue problem for the matrix A:

    >>> eigenvalues, _ = lobpcg(A, X, Y=Y, M=M, largest=False)
    >>> eigenvalues
    array([4., 5., 6.])

    Note that the vectors passed in Y are the eigenvectors of the 3 smallest
    eigenvalues. The results returned are orthogonal to those.
    """
    blockVectorX = X
    blockVectorY = Y
    residualTolerance = tol
    if maxiter is None:
        maxiter = 20

    if blockVectorY is not None:
        sizeY = blockVectorY.shape[1]
    else:
        sizeY = 0

    # Block size.
    if len(blockVectorX.shape) != 2:
        raise ValueError("expected rank-2 array for argument X")

    n, sizeX = blockVectorX.shape

    if verbosityLevel:
        aux = "Solving "
        if B is None:
            aux += "standard"
        else:
            aux += "generalized"
        aux += " eigenvalue problem with"
        if M is None:
            aux += "out"
        aux += " preconditioning\n\n"
        aux += "matrix size %d\n" % n
        aux += "block size %d\n\n" % sizeX
        if blockVectorY is None:
            aux += "No constraints\n\n"
        else:
            if sizeY > 1:
                aux += "%d constraints\n\n" % sizeY
            else:
                aux += "%d constraint\n\n" % sizeY
        print(aux)

    if (n - sizeY) < (5 * sizeX):
        warnings.warn(
            f"The problem size {n} minus the constraints size {sizeY} "
            f"is too small relative to the block size {sizeX}. "
            f"Using a dense eigensolver instead of LOBPCG.",
            UserWarning, stacklevel=2
        )

        sizeX = min(sizeX, n)

        if blockVectorY is not None:
            raise NotImplementedError(
                "The dense eigensolver does not support constraints."
            )

        # Define the closed range of indices of eigenvalues to return.
        if largest:
            eigvals = (n - sizeX, n - 1)
        else:
            eigvals = (0, sizeX - 1)

        if isinstance(A, LinearOperator):
            A = A(np.eye(n, dtype=A.dtype))
        elif isspmatrix(A):
            A = A.toarray()
        else:
            A = np.asarray(A)

        if B is not None:
            if isinstance(B, LinearOperator):
                B = B(np.eye(n, dtype=B.dtype))
            elif isspmatrix(B):
                B = B.toarray()
            else:
                B = np.asarray(B)

        vals, vecs = eigh(A,
                          B,
                          eigvals=eigvals,
                          check_finite=False)
        if largest:
            # Reverse order to be compatible with eigs() in 'LM' mode.
            vals = vals[::-1]
            vecs = vecs[:, ::-1]

        return vals, vecs

    if (residualTolerance is None) or (residualTolerance <= 0.0):
        residualTolerance = np.sqrt(1e-15) * n

    A = _makeOperator(A, (n, n))
    B = _makeOperator(B, (n, n))
    M = _makeOperator(M, (n, n))

    # Apply constraints to X.
    if blockVectorY is not None:

        if B is not None:
            blockVectorBY = B(blockVectorY)
        else:
            blockVectorBY = blockVectorY

        # gramYBY is a dense array.
        gramYBY = np.dot(blockVectorY.T.conj(), blockVectorBY)
        try:
            # gramYBY is a Cholesky factor from now on...
            gramYBY = cho_factor(gramYBY)
        except LinAlgError as e:
            raise ValueError("Linearly dependent constraints") from e

        _applyConstraints(blockVectorX, gramYBY, blockVectorBY, blockVectorY)

    ##
    # B-orthonormalize X.
    blockVectorX, blockVectorBX = _b_orthonormalize(B, blockVectorX)
    if blockVectorX is None:
        raise ValueError("Linearly dependent initial approximations")

    ##
    # Compute the initial Ritz vectors: solve the eigenproblem.
    blockVectorAX = A(blockVectorX)
    gramXAX = np.dot(blockVectorX.T.conj(), blockVectorAX)

    _lambda, eigBlockVector = eigh(gramXAX, check_finite=False)
    ii = _get_indx(_lambda, sizeX, largest)
    _lambda = _lambda[ii]

    eigBlockVector = np.asarray(eigBlockVector[:, ii])
    blockVectorX = np.dot(blockVectorX, eigBlockVector)
    blockVectorAX = np.dot(blockVectorAX, eigBlockVector)
    if B is not None:
        blockVectorBX = np.dot(blockVectorBX, eigBlockVector)

    ##
    # Active index set.
    activeMask = np.ones((sizeX,), dtype=bool)

    lambdaHistory = [_lambda]
    residualNormsHistory = []

    previousBlockSize = sizeX
    ident = np.eye(sizeX, dtype=A.dtype)
    ident0 = np.eye(sizeX, dtype=A.dtype)

    ##
    # Main iteration loop.

    blockVectorP = None  # set during iteration
    blockVectorAP = None
    blockVectorBP = None

    iterationNumber = -1
    restart = True
    explicitGramFlag = False
    while iterationNumber < maxiter:
        iterationNumber += 1
        if verbosityLevel > 0:
            print("-"*50)
            print(f"iteration {iterationNumber}")

        if B is not None:
            aux = blockVectorBX * _lambda[np.newaxis, :]
        else:
            aux = blockVectorX * _lambda[np.newaxis, :]

        blockVectorR = blockVectorAX - aux

        aux = np.sum(blockVectorR.conj() * blockVectorR, 0)
        residualNorms = np.sqrt(aux)

        residualNormsHistory.append(residualNorms)

        ii = np.where(residualNorms > residualTolerance, True, False)
        activeMask = activeMask & ii
        if verbosityLevel > 2:
            print(activeMask)

        currentBlockSize = activeMask.sum()
        if currentBlockSize != previousBlockSize:
            previousBlockSize = currentBlockSize
            ident = np.eye(currentBlockSize, dtype=A.dtype)

        if currentBlockSize == 0:
            break

        if verbosityLevel > 0:
            print(f"current block size: {currentBlockSize}")
            print(f"eigenvalue(s):\n{_lambda}")
            print(f"residual norm(s):\n{residualNorms}")
        if verbosityLevel > 10:
            print(eigBlockVector)

        activeBlockVectorR = _as2d(blockVectorR[:, activeMask])

        if iterationNumber > 0:
            activeBlockVectorP = _as2d(blockVectorP[:, activeMask])
            activeBlockVectorAP = _as2d(blockVectorAP[:, activeMask])
            if B is not None:
                activeBlockVectorBP = _as2d(blockVectorBP[:, activeMask])

        if M is not None:
            # Apply preconditioner T to the active residuals.
            activeBlockVectorR = M(activeBlockVectorR)

        ##
        # Apply constraints to the preconditioned residuals.
        if blockVectorY is not None:
            _applyConstraints(activeBlockVectorR,
                              gramYBY,
                              blockVectorBY,
                              blockVectorY)

        ##
        # B-orthogonalize the preconditioned residuals to X.
        if B is not None:
            activeBlockVectorR = activeBlockVectorR - (
                blockVectorX @
                (blockVectorBX.T.conj() @ activeBlockVectorR)
            )
        else:
            activeBlockVectorR = activeBlockVectorR - (
                blockVectorX @
                (blockVectorX.T.conj() @ activeBlockVectorR)
            )

        ##
        # B-orthonormalize the preconditioned residuals.
        aux = _b_orthonormalize(B, activeBlockVectorR)
        activeBlockVectorR, activeBlockVectorBR = aux

        if activeBlockVectorR is None:
            warnings.warn(
                f"Failed at iteration {iterationNumber} with accuracies "
                f"{residualNorms}\n not reaching the requested "
                f"tolerance {residualTolerance}.",
                UserWarning, stacklevel=2
            )
            break
        activeBlockVectorAR = A(activeBlockVectorR)

        if iterationNumber > 0:
            if B is not None:
                aux = _b_orthonormalize(
                    B, activeBlockVectorP, activeBlockVectorBP, retInvR=True
                )
                activeBlockVectorP, activeBlockVectorBP, invR, normal = aux
            else:
                aux = _b_orthonormalize(B, activeBlockVectorP, retInvR=True)
                activeBlockVectorP, _, invR, normal = aux
            # Function _b_orthonormalize returns None if Cholesky fails
            if activeBlockVectorP is not None:
                activeBlockVectorAP = activeBlockVectorAP / normal
                activeBlockVectorAP = np.dot(activeBlockVectorAP, invR)
                restart = False
            else:
                restart = True

        ##
        # Perform the Rayleigh Ritz Procedure:
        # Compute symmetric Gram matrices:

        if activeBlockVectorAR.dtype == "float32":
            myeps = 1
        elif activeBlockVectorR.dtype == "float32":
            myeps = 1e-4
        else:
            myeps = 1e-8

        if residualNorms.max() > myeps and not explicitGramFlag:
            explicitGramFlag = False
        else:
            # Once explicitGramFlag, forever explicitGramFlag.
            explicitGramFlag = True

        # Shared memory assingments to simplify the code
        if B is None:
            blockVectorBX = blockVectorX
            activeBlockVectorBR = activeBlockVectorR
            if not restart:
                activeBlockVectorBP = activeBlockVectorP

        # Common submatrices:
        gramXAR = np.dot(blockVectorX.T.conj(), activeBlockVectorAR)
        gramRAR = np.dot(activeBlockVectorR.T.conj(), activeBlockVectorAR)

        if explicitGramFlag:
            gramRAR = (gramRAR + gramRAR.T.conj()) / 2
            gramXAX = np.dot(blockVectorX.T.conj(), blockVectorAX)
            gramXAX = (gramXAX + gramXAX.T.conj()) / 2
            gramXBX = np.dot(blockVectorX.T.conj(), blockVectorBX)
            gramRBR = np.dot(activeBlockVectorR.T.conj(), activeBlockVectorBR)
            gramXBR = np.dot(blockVectorX.T.conj(), activeBlockVectorBR)
        else:
            gramXAX = np.diag(_lambda)
            gramXBX = ident0
            gramRBR = ident
            gramXBR = np.zeros((sizeX, currentBlockSize), dtype=A.dtype)

        def _handle_gramA_gramB_verbosity(gramA, gramB):
            if verbosityLevel > 0:
                _report_nonhermitian(gramA, "gramA")
                _report_nonhermitian(gramB, "gramB")
            if verbosityLevel > 10:
                # Note: not documented, but leave it in here for now
                np.savetxt("gramA.txt", gramA)
                np.savetxt("gramB.txt", gramB)

        if not restart:
            gramXAP = np.dot(blockVectorX.T.conj(), activeBlockVectorAP)
            gramRAP = np.dot(activeBlockVectorR.T.conj(), activeBlockVectorAP)
            gramPAP = np.dot(activeBlockVectorP.T.conj(), activeBlockVectorAP)
            gramXBP = np.dot(blockVectorX.T.conj(), activeBlockVectorBP)
            gramRBP = np.dot(activeBlockVectorR.T.conj(), activeBlockVectorBP)
            if explicitGramFlag:
                gramPAP = (gramPAP + gramPAP.T.conj()) / 2
                gramPBP = np.dot(activeBlockVectorP.T.conj(),
                                 activeBlockVectorBP)
            else:
                gramPBP = ident

            gramA = bmat(
                [
                    [gramXAX, gramXAR, gramXAP],
                    [gramXAR.T.conj(), gramRAR, gramRAP],
                    [gramXAP.T.conj(), gramRAP.T.conj(), gramPAP],
                ]
            )
            gramB = bmat(
                [
                    [gramXBX, gramXBR, gramXBP],
                    [gramXBR.T.conj(), gramRBR, gramRBP],
                    [gramXBP.T.conj(), gramRBP.T.conj(), gramPBP],
                ]
            )

            _handle_gramA_gramB_verbosity(gramA, gramB)

            try:
                _lambda, eigBlockVector = eigh(gramA,
                                               gramB,
                                               check_finite=False)
            except LinAlgError:
                # try again after dropping the direction vectors P from RR
                restart = True

        if restart:
            gramA = bmat([[gramXAX, gramXAR], [gramXAR.T.conj(), gramRAR]])
            gramB = bmat([[gramXBX, gramXBR], [gramXBR.T.conj(), gramRBR]])

            _handle_gramA_gramB_verbosity(gramA, gramB)

            try:
                _lambda, eigBlockVector = eigh(gramA,
                                               gramB,
                                               check_finite=False)
            except LinAlgError as e:
                raise ValueError("eigh has failed in lobpcg iterations") from e

        ii = _get_indx(_lambda, sizeX, largest)
        if verbosityLevel > 10:
            print(ii)
            print(f"lambda:\n{_lambda}")

        _lambda = _lambda[ii]
        eigBlockVector = eigBlockVector[:, ii]

        lambdaHistory.append(_lambda)

        if verbosityLevel > 10:
            print(f"lambda:\n{_lambda}")
        #         # Normalize eigenvectors!
        #         aux = np.sum( eigBlockVector.conj() * eigBlockVector, 0 )
        #         eigVecNorms = np.sqrt( aux )
        #         eigBlockVector = eigBlockVector / eigVecNorms[np.newaxis, :]
        #         eigBlockVector, aux = _b_orthonormalize( B, eigBlockVector )

        if verbosityLevel > 10:
            print(eigBlockVector)

        # Compute Ritz vectors.
        if B is not None:
            if not restart:
                eigBlockVectorX = eigBlockVector[:sizeX]
                eigBlockVectorR = eigBlockVector[sizeX:
                                                 sizeX + currentBlockSize]
                eigBlockVectorP = eigBlockVector[sizeX + currentBlockSize:]

                pp = np.dot(activeBlockVectorR, eigBlockVectorR)
                pp += np.dot(activeBlockVectorP, eigBlockVectorP)

                app = np.dot(activeBlockVectorAR, eigBlockVectorR)
                app += np.dot(activeBlockVectorAP, eigBlockVectorP)

                bpp = np.dot(activeBlockVectorBR, eigBlockVectorR)
                bpp += np.dot(activeBlockVectorBP, eigBlockVectorP)
            else:
                eigBlockVectorX = eigBlockVector[:sizeX]
                eigBlockVectorR = eigBlockVector[sizeX:]

                pp = np.dot(activeBlockVectorR, eigBlockVectorR)
                app = np.dot(activeBlockVectorAR, eigBlockVectorR)
                bpp = np.dot(activeBlockVectorBR, eigBlockVectorR)

            if verbosityLevel > 10:
                print(pp)
                print(app)
                print(bpp)

            blockVectorX = np.dot(blockVectorX, eigBlockVectorX) + pp
            blockVectorAX = np.dot(blockVectorAX, eigBlockVectorX) + app
            blockVectorBX = np.dot(blockVectorBX, eigBlockVectorX) + bpp

            blockVectorP, blockVectorAP, blockVectorBP = pp, app, bpp

        else:
            if not restart:
                eigBlockVectorX = eigBlockVector[:sizeX]
                eigBlockVectorR = eigBlockVector[sizeX:
                                                 sizeX + currentBlockSize]
                eigBlockVectorP = eigBlockVector[sizeX + currentBlockSize:]

                pp = np.dot(activeBlockVectorR, eigBlockVectorR)
                pp += np.dot(activeBlockVectorP, eigBlockVectorP)

                app = np.dot(activeBlockVectorAR, eigBlockVectorR)
                app += np.dot(activeBlockVectorAP, eigBlockVectorP)
            else:
                eigBlockVectorX = eigBlockVector[:sizeX]
                eigBlockVectorR = eigBlockVector[sizeX:]

                pp = np.dot(activeBlockVectorR, eigBlockVectorR)
                app = np.dot(activeBlockVectorAR, eigBlockVectorR)

            if verbosityLevel > 10:
                print(pp)
                print(app)

            blockVectorX = np.dot(blockVectorX, eigBlockVectorX) + pp
            blockVectorAX = np.dot(blockVectorAX, eigBlockVectorX) + app

            blockVectorP, blockVectorAP = pp, app

    if B is not None:
        aux = blockVectorBX * _lambda[np.newaxis, :]

    else:
        aux = blockVectorX * _lambda[np.newaxis, :]

    blockVectorR = blockVectorAX - aux

    aux = np.sum(blockVectorR.conj() * blockVectorR, 0)
    residualNorms = np.sqrt(aux)

    if np.max(residualNorms) > residualTolerance:
        warnings.warn(
            f"Exited at iteration {iterationNumber} with accuracies \n"
            f"{residualNorms}\n"
            f"not reaching the requested tolerance {residualTolerance}.",
            UserWarning, stacklevel=2
        )

    # Future work: Need to add Postprocessing here:
    # Making sure eigenvectors "exactly" satisfy the blockVectorY constrains?
    # Making sure eigenvecotrs are "exactly" othonormalized by final "exact" RR
    # Keeping the best iterates in case of divergence

    if verbosityLevel > 0:
        print(f"Final eigenvalue(s):\n{_lambda}")
        print(f"Final residual norm(s):\n{residualNorms}")

    if retLambdaHistory:
        if retResidualNormsHistory:
            return _lambda, blockVectorX, lambdaHistory, residualNormsHistory
        else:
            return _lambda, blockVectorX, lambdaHistory
    else:
        if retResidualNormsHistory:
            return _lambda, blockVectorX, residualNormsHistory
        else:
            return _lambda, blockVectorX
