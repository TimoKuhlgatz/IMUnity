"""Known-answer checks for the readers of Trento's authors' products (datasets/trento_final.py):
units and the reference's convention are what the docstring states, read off the data themselves.
- accelerometer in m/s^2: the median length of every sensor in the N-pose is gravity;
- gyroscope converted from deg/s: once the two streams are put on one time base (the products carry no common
  time), the gyroscope's speed and the reference's rotation speed agree in size (rms ratio near 1), which a wrong
  factor (57.3 or 1/57.3) could not give;
- quaternions [w x y z] mapping segment to world: with that convention one constant rotation maps the gyroscope onto
  the reference's rate in segment axes with a small misfit; read the other way round it fits far worse.
Skipped where the deposit is not on this machine."""
import numpy as np
import pytest

from imunity.errors.e18_mounting import fit_gyroscope_to_reference
from imunity.datasets.trento_final import ROOT, TrentoFinalDepositedLoader
from imunity.rotations import reference_angular_rate

pytestmark = pytest.mark.skipif(not ROOT.exists(), reason='Trento deposit not on this machine')
TRIAL = '3/t11-task-1'  # elbow flexion-extension with neutral forearm: the forearm turns fast about one axis


def _aligned_forearm(trial=TRIAL):
    rec = TrentoFinalDepositedLoader().load(trial)
    seg = rec.segments['forearm']
    _, w = reference_angular_rate(seg.reference.orientation, seg.reference.rate_hz)
    ref_speed, imu_speed = np.linalg.norm(w, axis=1), np.linalg.norm(seg.imu.gyr, axis=1)
    n = min(len(ref_speed), len(imu_speed))
    best = max(range(-600, 601), key=lambda lag: np.corrcoef(
        ref_speed[max(0, lag):n + min(0, lag)], imu_speed[max(0, -lag):n - max(0, lag)])[0, 1])
    seg.imu.start_s = best / seg.imu.rate_hz  # IMU sample i at the time of reference frame i + lag
    return seg, ref_speed, imu_speed, best


def test_accelerometer_reads_gravity_at_rest():
    rec = TrentoFinalDepositedLoader().load('3/t01-n-pose')
    for seg in rec.segments.values():
        assert abs(np.median(np.linalg.norm(seg.imu.acc, axis=1)) - 9.81) < 0.3


def test_gyroscope_is_in_rad_per_s():
    seg, ref_speed, imu_speed, lag = _aligned_forearm()
    n = min(len(ref_speed), len(imu_speed))
    r = ref_speed[max(0, lag):n + min(0, lag)]
    g = imu_speed[max(0, -lag):n - max(0, lag)]
    assert np.corrcoef(r, g)[0, 1] > 0.9
    assert 0.8 < np.sqrt(np.mean(g ** 2) / np.mean(r ** 2)) < 1.25


def test_reference_maps_segment_to_world():
    """The forearm turns mostly about one axis, where both readings look alike, so the test pools the trials in
    which it moves fast (rate spread above 60 deg/s): the declared reading fits better in every
    one (ratio 0.50-0.64), while the slow tasks give 0.85-0.96."""
    ratios = []
    for trial in ('3/t11-task-1', '3/t15-task-5', '3/t18-task-8', '3/t19-task-9', '3/t20-task-10', '5/t20-task-10'):
        seg, *_ = _aligned_forearm(trial)
        assert seg.reference.convention == 'segment_to_world'
        declared = fit_gyroscope_to_reference(seg)
        seg.reference.convention = 'world_to_segment'
        other = fit_gyroscope_to_reference(seg)
        assert declared.rate_spread_dps > 60 and declared.residual_dps < 0.3 * declared.rate_spread_dps
        ratios.append(declared.residual_dps / other.residual_dps)
    assert max(ratios) < 0.75 and np.median(ratios) < 0.7
