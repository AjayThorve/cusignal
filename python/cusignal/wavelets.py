# Copyright (c) 2019-2020, NVIDIA CORPORATION.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import cupy as cp
from .signaltools import convolve


def qmf(hk):
    """
    Return high-pass qmf filter from low-pass

    Parameters
    ----------
    hk : array_like
        Coefficients of high-pass filter.

    """
    N = len(hk) - 1
    asgn = [{0: 1, 1: -1}[k % 2] for k in range(N + 1)]
    return hk[::-1] * cp.array(asgn)

    """
    Return (x, phi, psi) at dyadic points ``K/2**J`` from filter coefficients.

    Parameters
    ----------
    hk : array_like
        Coefficients of low-pass filter.
    J : int, optional
        Values will be computed at grid points ``K/2**J``. Default is 7.

    Returns
    -------
    x : ndarray
        The dyadic points ``K/2**J`` for ``K=0...N * (2**J)-1`` where
        ``len(hk) = len(gk) = N+1``.
    phi : ndarray
        The scaling function ``phi(x)`` at `x`:
        ``phi(x) = sum(hk * phi(2x-k))``, where k is from 0 to N.
    psi : ndarray, optional
        The wavelet function ``psi(x)`` at `x`:
        ``phi(x) = sum(gk * phi(2x-k))``, where k is from 0 to N.
        `psi` is only returned if `gk` is not None.

    Notes
    -----
    The algorithm uses the vector cascade algorithm described by Strang and
    Nguyen in "Wavelets and Filter Banks".  It builds a dictionary of values
    and slices for quick reuse.  Then inserts vectors into final vector at the
    end.

    """
    N = len(hk) - 1

    if (J > 30 - cp.log2(N + 1)):
        raise ValueError("Too many levels.")
    if (J < 1):
        raise ValueError("Too few levels.")

    # construct matrices needed
    nn, kk = cp.ogrid[:N, :N]
    s2 = cp.sqrt(2)
    # append a zero so that take works
    thk = cp.r_[hk, 0]
    gk = qmf(hk)
    tgk = cp.r_[gk, 0]

    indx1 = cp.clip(2 * nn - kk, -1, N + 1)
    indx2 = cp.clip(2 * nn - kk + 1, -1, N + 1)
    m = cp.zeros((2, 2, N, N), 'd')
    m[0, 0] = cp.take(thk, indx1, 0)
    m[0, 1] = cp.take(thk, indx2, 0)
    m[1, 0] = cp.take(tgk, indx1, 0)
    m[1, 1] = cp.take(tgk, indx2, 0)
    m *= s2

    # construct the grid of points
    x = cp.arange(0, N * (1 << J), dtype=float) / (1 << J)
    phi = 0 * x

    psi = 0 * x

    # find phi0, and phi1
    lam, v = eig(m[0, 0])
    ind = cp.argmin(cp.absolute(lam - 1))
    # a dictionary with a binary representation of the
    #   evaluation points x < 1 -- i.e. position is 0.xxxx
    v = cp.real(v[:, ind])
    # need scaling function to integrate to 1 so find
    #  eigenvector normalized to sum(v,axis=0)=1
    sm = cp.sum(v)
    if sm < 0:  # need scaling function to integrate to 1
        v = -v
        sm = -sm
    bitdic = {'0': v / sm}
    bitdic['1'] = cp.dot(m[0, 1], bitdic['0'])
    step = 1 << J
    phi[::step] = bitdic['0']
    phi[(1 << (J - 1))::step] = bitdic['1']
    psi[::step] = cp.dot(m[1, 0], bitdic['0'])
    psi[(1 << (J - 1))::step] = cp.dot(m[1, 1], bitdic['0'])
    # descend down the levels inserting more and more values
    #  into bitdic -- store the values in the correct location once we
    #  have computed them -- stored in the dictionary
    #  for quicker use later.
    prevkeys = ['1']
    for level in range(2, J + 1):
        newkeys = ['%d%s' % (xx, yy) for xx in [0, 1] for yy in prevkeys]
        fac = 1 << (J - level)
        for key in newkeys:
            # convert key to number
            num = 0
            for pos in range(level):
                if key[pos] == '1':
                    num += (1 << (level - 1 - pos))
            pastphi = bitdic[key[1:]]
            ii = int(key[0])
            temp = cp.dot(m[0, ii], pastphi)
            bitdic[key] = temp
            phi[num * fac::step] = temp
            psi[num * fac::step] = cp.dot(m[1, ii], pastphi)
        prevkeys = newkeys

    return x, phi, psi


def morlet(M, w=5.0, s=1.0, complete=True):
    """
    Complex Morlet wavelet.

    Parameters
    ----------
    M : int
        Length of the wavelet.
    w : float, optional
        Omega0. Default is 5
    s : float, optional
        Scaling factor, windowed from ``-s*2*pi`` to ``+s*2*pi``. Default is 1.
    complete : bool, optional
        Whether to use the complete or the standard version.

    Returns
    -------
    morlet : (M,) ndarray

    See Also
    --------
    scipy.signal.gausspulse

    Notes
    -----
    The standard version::

        pi**-0.25 * exp(1j*w*x) * exp(-0.5*(x**2))

    This commonly used wavelet is often referred to simply as the
    Morlet wavelet.  Note that this simplified version can cause
    admissibility problems at low values of `w`.

    The complete version::

        pi**-0.25 * (exp(1j*w*x) - exp(-0.5*(w**2))) * exp(-0.5*(x**2))

    This version has a correction
    term to improve admissibility. For `w` greater than 5, the
    correction term is negligible.

    Note that the energy of the return wavelet is not normalised
    according to `s`.

    The fundamental frequency of this wavelet in Hz is given
    by ``f = 2*s*w*r / M`` where `r` is the sampling rate.

    Note: This function was created before `cwt` and is not compatible
    with it.

    """
    x = cp.linspace(-s * 2 * cp.pi, s * 2 * cp.pi, M)
    output = cp.exp(1j * w * x)

    if complete:
        output -= cp.exp(-0.5 * (w**2))

    output *= cp.exp(-0.5 * (x**2)) * cp.pi**(-0.25)

    return output


def ricker(points, a):
    """
    Return a Ricker wavelet, also known as the "Mexican hat wavelet".

    It models the function:

        ``A (1 - x^2/a^2) exp(-x^2/2 a^2)``,

    where ``A = 2/sqrt(3a)pi^1/4``.

    Parameters
    ----------
    points : int
        Number of points in `vector`.
        Will be centered around 0.
    a : scalar
        Width parameter of the wavelet.

    Returns
    -------
    vector : (N,) ndarray
        Array of length `points` in shape of ricker curve.

    Examples
    --------
    >>> from scipy import signal
    >>> import matplotlib.pyplot as plt

    >>> points = 100
    >>> a = 4.0
    >>> vec2 = signal.ricker(points, a)
    >>> print(len(vec2))
    100
    >>> plt.plot(vec2)
    >>> plt.show()

    """
    A = 2 / (cp.sqrt(3 * a) * (cp.pi**0.25))
    wsq = a**2
    vec = cp.arange(0, points) - (points - 1.0) / 2
    xsq = vec**2
    mod = (1 - xsq / wsq)
    gauss = cp.exp(-xsq / (2 * wsq))
    total = A * mod * gauss
    return total


def cwt(data, wavelet, widths):
    """
    Continuous wavelet transform.

    Performs a continuous wavelet transform on `data`,
    using the `wavelet` function. A CWT performs a convolution
    with `data` using the `wavelet` function, which is characterized
    by a width parameter and length parameter.

    Parameters
    ----------
    data : (N,) ndarray
        data on which to perform the transform.
    wavelet : function
        Wavelet function, which should take 2 arguments.
        The first argument is the number of points that the returned vector
        will have (len(wavelet(length,width)) == length).
        The second is a width parameter, defining the size of the wavelet
        (e.g. standard deviation of a gaussian). See `ricker`, which
        satisfies these requirements.
    widths : (M,) sequence
        Widths to use for transform.

    Returns
    -------
    cwt: (M, N) ndarray
        Will have shape of (len(widths), len(data)).

    Notes
    -----
    ::

        length = min(10 * width[ii], len(data))
        cwt[ii,:] = signal.convolve(data, wavelet(length,
                                    width[ii]), mode='same')

    Examples
    --------
    >>> from scipy import signal
    >>> import matplotlib.pyplot as plt
    >>> t = np.linspace(-1, 1, 200, endpoint=False)
    >>> sig  = np.cos(2 * np.pi * 7 * t) + signal.gausspulse(t - 0.4, fc=2)
    >>> widths = np.arange(1, 31)
    >>> cwtmatr = signal.cwt(sig, signal.ricker, widths)
    >>> plt.imshow(cwtmatr, extent=[-1, 1, 31, 1], cmap='PRGn', aspect='auto',
    ...            vmax=abs(cwtmatr).max(), vmin=-abs(cwtmatr).max())
    >>> plt.show()

    """
    output = cp.zeros([len(widths), len(data)])
    for ind, width in enumerate(widths):
        wavelet_data = wavelet(min(10 * width, len(data)), width)
        output[ind, :] = convolve(data, wavelet_data,
                                  mode='same')
    return output
