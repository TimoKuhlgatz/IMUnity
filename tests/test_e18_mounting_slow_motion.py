"""Known-answer tests for lowering the mounting fit's rate limit (error 18) from 17 deg/s, whose
origin is undocumented, to the rest limit of error 17 (7.5 deg/s).
REEM-C's pelvis moves continuously at 5-13 deg/s once the rates are averaged over 0.25 s, so no sample passes
17 deg/s and the segment stays uncorrected. The tests pass the limit explicitly to window_fit,
so they check both limits on the same recordings:
- slow motion: the known mounting is recovered with the rest limit and not fitted at all with 17 deg/s;
- the stated uncertainty still holds the true error for slow, jittered motion;
- a still segment with marker jitter (the risk of a lower limit: averaged jitter can pass the rest limit) gives no fit
  that claims to be precise while it is wrong;
- fast clean motion gives the same rotation with either limit."""
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from imunity.errors import e18_mounting as M
from imunity.synthetic import make_recording

MOUNTING = Rotation.from_euler('xyz', [20, -10, 90], degrees=True)
REST_DPS = M.MIN_RATE_DPS  # rest limit of error 17 (gyroscope offset), the fit's rate limit
OLD_DPS = 17.0


def _error_deg(fit):
    return float(np.degrees((Rotation.from_matrix(fit.sensor_to_segment) * MOUNTING).magnitude()))


def _slow(seed, peak_dps=15.0, jitter_deg=0.0, still=False):
    """20 s at 100 Hz with the known mounting; motion peaking at peak_dps (REEM-C pelvis: 10-13 deg/s averaged);
    the reference wobbles by jitter_deg per axis and frame. still=True: the segment does not move at all."""
    rec = make_recording(duration_s=20.0, imu_rate_hz=100.0, reference_rate_hz=100.0, mounting=MOUNTING, seed=seed,
                         peak_dps=1e-9 if still else peak_dps)
    rng = np.random.default_rng(seed + 11)
    for seg in rec.segments.values():
        if jitter_deg:
            noise = np.radians(jitter_deg) * rng.standard_normal((len(seg.reference), 3))
            seg.reference.orientation = seg.reference.orientation * Rotation.from_rotvec(noise)
        seg.imu.gyr = seg.imu.gyr + np.radians(0.2) * rng.standard_normal(seg.imu.gyr.shape)  # gyroscope noise
    return rec


@pytest.mark.parametrize('seed', range(3))
def test_slow_motion_is_fitted_with_the_rest_limit_and_lost_with_17(seed):
    seg = _slow(seed, jitter_deg=0.1).segments['upper']
    old = M.window_fit(seg, 0.0, min_rate_dps=OLD_DPS)
    new = M.window_fit(seg, 0.0, min_rate_dps=REST_DPS)
    assert old['fit'] is None or old['uncertainty_deg'] >= 180.0
    assert new['fit'] is not None
    err = _error_deg(new['fit'])
    assert err <= new['uncertainty_deg'] and new['uncertainty_deg'] < 30.0


def test_uncertainty_holds_the_true_error_for_slow_jittered_motion():
    """Coverage over 20 slow recordings with marker jitter of 0.3 deg per frame, both segments: the true error lies
    inside the stated 95 % uncertainty in at least 90 % of the fits."""
    inside = []
    for seed in range(20):
        for seg in _slow(200 + seed, jitter_deg=0.3).segments.values():
            p = M.window_fit(seg, 0.0, min_rate_dps=REST_DPS)
            if p['fit'] is not None:
                inside.append(_error_deg(p['fit']) <= p['uncertainty_deg'])
    assert len(inside) >= 30 and np.mean(inside) >= 0.9


@pytest.mark.parametrize('jitter_deg', [0.3, 1.0, 2.0])
def test_still_segment_with_jitter_gives_no_confident_wrong_fit(jitter_deg):
    """A segment that does not move, with marker jitter up to 2 deg per frame (Camargo-like): averaged jitter can pass
    the rest limit, but whatever is fitted must state an uncertainty that holds its error."""
    for seed in range(10):
        for seg in _slow(300 + seed, jitter_deg=jitter_deg, still=True).segments.values():
            p = M.window_fit(seg, 0.0, min_rate_dps=REST_DPS)
            if p['fit'] is not None:
                assert _error_deg(p['fit']) <= p['uncertainty_deg']


@pytest.mark.parametrize('seed', range(5))
def test_fast_clean_motion_gives_the_same_rotation_with_either_limit(seed):
    seg = make_recording(duration_s=20.0, imu_rate_hz=200.0, reference_rate_hz=200.0, mounting=MOUNTING,
                         seed=seed).segments['upper']
    old = M.window_fit(seg, 0.0, min_rate_dps=OLD_DPS)
    new = M.window_fit(seg, 0.0, min_rate_dps=REST_DPS)
    diff = np.degrees((Rotation.from_matrix(old['fit'].sensor_to_segment).inv()
                       * Rotation.from_matrix(new['fit'].sensor_to_segment)).magnitude())
    assert diff < 0.5 and _error_deg(new['fit']) <= min(new['uncertainty_deg'], 2.0)
