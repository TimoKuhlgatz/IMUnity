"""Known-answer tests for the mounting correction (error 18, mounting rotation):
- the fit's rate limit is the rest limit of error 17 (7.5 deg/s), not 17 deg/s;
- a fit that knows nothing in any direction (95 % uncertainty 180 deg in all three) is not applied: without a pool
  the segment stays uncorrected and is reported; a fit known in at least one direction (a segment turning about one
  axis only) is still applied;
- the own-window search counts a segment as moving above twice the rest limit (15 deg/s), so that marker jitter of a
  still segment does not start it.
mount_trial runs without any pool (one trial per participant)."""
import numpy as np
from scipy.spatial.transform import Rotation

from imunity.errors import e18_mounting as M
from imunity.errors.e17_gyroscope_offset import MAX_REF_DPS
from imunity.synthetic import make_recording

MOUNTING = Rotation.from_euler('xyz', [20, -10, 90], degrees=True)


def _error_deg(matrix):
    return float(np.degrees((Rotation.from_matrix(matrix) * MOUNTING).magnitude()))


def _with_still_lower(seed=5, still_s=None, jitter_deg=1.0):
    """40 s at 100 Hz, 'upper' moves; 'lower' holds still (whole trial, or its first still_s) with marker jitter of
    jitter_deg per frame and gyroscope noise, then moves."""
    rec = make_recording(duration_s=40.0, imu_rate_hz=100.0, reference_rate_hz=100.0, mounting=MOUNTING, seed=seed)
    seg = rec.segments['lower']
    rng = np.random.default_rng(seed + 1)
    n = len(seg.reference)
    hold = n if still_s is None else int(still_s * seg.reference.rate_hz)
    q = seg.reference.orientation.as_quat()
    q[:hold] = q[hold - 1] if hold < n else q[0]
    noise = np.radians(jitter_deg) * rng.standard_normal((n, 3))
    seg.reference.orientation = Rotation.from_quat(q) * Rotation.from_rotvec(noise)
    k = int(hold / seg.reference.rate_hz * seg.imu.rate_hz)
    seg.imu.gyr[:k] = np.radians(0.2) * rng.standard_normal((min(k, len(seg.imu.gyr)), 3))
    return rec


def _log(rec):
    out = M.mount_trial(rec, None, participant='P', trial='t')
    return out, out.corrections[-1].parameters


def test_rate_limit_is_the_rest_limit_and_the_own_window_twice_it():
    assert M.MIN_RATE_DPS == MAX_REF_DPS == 7.5
    assert M.OWN_WINDOW_DPS == 2 * MAX_REF_DPS


def test_still_segment_without_pool_stays_uncorrected():
    for jitter in (0.3, 1.0, 2.0):
        _, log = _log(_with_still_lower(jitter_deg=jitter))
        assert 'lower' in log['not_corrected'] and 'upper' not in log['not_corrected']


def test_jitter_of_a_still_segment_does_not_start_the_own_window():
    for jitter in (0.3, 1.0, 2.0):
        _, log = _log(_with_still_lower(jitter_deg=jitter))
        assert 'lower' not in log['own_window']


def test_segment_moving_later_takes_its_own_window_and_is_right():
    out, log = _log(_with_still_lower(still_s=15.0))
    assert 'lower' in log['own_window'] and 'lower' not in log['not_corrected']
    w = log['weights']['lower']
    rotated = out.segments['lower'].imu.gyr
    raw = _with_still_lower(still_s=15.0).segments['lower'].imu.gyr
    applied = Rotation.align_vectors(rotated[2000:], raw[2000:])[0].as_matrix()  # the rotation the stage applied
    assert _error_deg(applied) <= w['applied_uncertainty_deg']


def test_fit_known_in_two_directions_is_still_applied():
    """A segment turning about one axis only: two directions known to 0.1 deg, the third not at all. It is
    applied, not dropped."""
    rec = make_recording(duration_s=30.0, imu_rate_hz=100.0, reference_rate_hz=100.0, seed=2)
    seg = rec.segments['lower']
    t = seg.reference.times
    ang = np.radians(40) * np.sin(2 * np.pi * 0.7 * t)
    seg.reference.orientation = Rotation.from_rotvec(np.c_[0 * ang, 0 * ang, ang])
    rate = np.radians(40) * 2 * np.pi * 0.7 * np.cos(2 * np.pi * 0.7 * seg.imu.times)
    seg.imu.gyr = MOUNTING.apply(np.c_[0 * rate, 0 * rate, rate])
    _, log = _log(rec)
    assert 'lower' not in log['not_corrected']
    assert log['weights']['lower']['own_uncertainty_deg'] >= 180.0  # unknown about the turning axis


def test_knows_nothing_reads_every_direction():
    assert M.knows_nothing(np.zeros((3, 3)))
    assert not M.knows_nothing(np.diag([0.0, 1e6, 1e6]))  # two directions known
    assert not M.knows_nothing(np.diag([1e6, 1e6, 1e6]))
