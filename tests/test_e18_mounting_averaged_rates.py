"""Known-answer tests for the mounting window fit on rates averaged over RATE_SPAN_S (error 18, mounting rotation): a
reference with marker-like frame jitter (frame-to-frame rates far above the gyroscope's, as on
Camargo thighs) emptied the old window (frames left out where reference and gyroscope speed differ by more than
300 deg/s, widened by 0.5 s); with both rates averaged over the span, the known mounting is recovered within the
stated uncertainty, and a clean recording stays exact."""
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from imunity.errors import e18_mounting as M
from imunity.synthetic import make_recording

MOUNTING = Rotation.from_euler('xyz', [20, -10, 90], degrees=True)


def _jittered(seed, sigma_deg=1.0, spike_share=0.03, spike_deg=8.0):
    """200 Hz recording with the known mounting; every reference frame wobbles by sigma_deg per axis, a share of
    frames jumps by spike_deg (marker jitter and brief mislabels; both come back on the next frame)."""
    rec = make_recording(duration_s=20.0, imu_rate_hz=200.0, reference_rate_hz=200.0, mounting=MOUNTING, seed=seed)
    rng = np.random.default_rng(seed + 7)
    for seg in rec.segments.values():
        n = len(seg.reference)
        noise = np.radians(sigma_deg) * rng.standard_normal((n, 3))
        jump = rng.random(n) < spike_share
        noise[jump] += np.radians(spike_deg) * rng.standard_normal((jump.sum(), 3))
        seg.reference.orientation = seg.reference.orientation * Rotation.from_rotvec(noise)
    return rec


def _error_deg(fit):
    return float(np.degrees((Rotation.from_matrix(fit.sensor_to_segment) * MOUNTING).magnitude()))


def test_jitter_exceeds_the_old_limit_and_empties_the_old_window():
    seg = _jittered(0).segments['upper']
    g, w = M.rate_pairs(seg, 'segment_to_world', window_s=10.0, window_start_s=0.0)  # old rule: frame to frame
    g2, w2 = M.rate_pairs(seg, 'segment_to_world', window_s=10.0, window_start_s=0.0, span_s=M.RATE_SPAN_S)
    assert len(g) < 0.2 * len(g2)  # the old exclusion leaves under a fifth of the samples the new rule keeps


@pytest.mark.parametrize('seed', range(3))
def test_known_mounting_is_recovered_through_jitter(seed):
    seg = _jittered(seed).segments['upper']
    p = M.window_fit(seg, 0.0)
    err = _error_deg(p['fit'])
    assert err < 3.0 and err <= p['uncertainty_deg']


def test_uncertainty_holds_the_true_error_under_jitter():
    """Coverage over 20 jittered recordings and both segments: the true error lies inside the stated 95 %
    uncertainty in at least 90 % of the fits."""
    inside = []
    for seed in range(20):
        for seg in _jittered(100 + seed, sigma_deg=1.5).segments.values():
            p = M.window_fit(seg, 0.0)
            inside.append(_error_deg(p['fit']) <= p['uncertainty_deg'])
    assert np.mean(inside) >= 0.9


def test_averaging_error_on_clean_recordings_stays_inside_the_uncertainty():
    """Averaging over the span is not exact when the turn axis changes within it (net rotation against mean rate):
    on clean synthetic motion (peaks 200 deg/s, axes changing up to 2 Hz) the window fit is off by 0.73 deg median,
    1.42 deg at most (20 fits; 0.11 deg at 0.1 s, 2.6 deg at 0.5 s). The stated uncertainty must
    hold it: every one of 40 clean fits."""
    for seed in range(20):
        for seg in make_recording(duration_s=20.0, imu_rate_hz=200.0, reference_rate_hz=200.0, mounting=MOUNTING,
                                  seed=seed).segments.values():
            p = M.window_fit(seg, 0.0)
            assert _error_deg(p['fit']) <= min(p['uncertainty_deg'], 2.0)


def test_invalid_reference_frames_are_left_out():
    seg = make_recording(duration_s=20.0, imu_rate_hz=200.0, reference_rate_hz=200.0, mounting=MOUNTING).segments['upper']
    seg.reference.valid[400:600] = False  # masked by a reference-only check
    t, g, w = M.averaged_rate_pairs(seg, 'segment_to_world', M.RATE_SPAN_S)
    span = M.RATE_SPAN_S
    touching = (t + span / 2 >= 400 / 200.0) & (t - span / 2 <= 599 / 200.0)
    assert np.isnan(w[touching]).all() and np.isfinite(w[~touching]).all()


def test_a_single_reference_jump_is_left_out():
    """A held pose that ends in one jump (faster than any human segment turns): every span holding the jump is left
    out, so the fit is not pulled off (without the guard about 100 deg)."""
    rec = make_recording(duration_s=30.0, imu_rate_hz=200.0, reference_rate_hz=200.0, mounting=MOUNTING, seed=4)
    seg = rec.segments['upper']
    q = seg.reference.orientation.as_quat()
    q[:1000] = q[0]  # held for 5 s, then jumps to the true pose
    seg.reference.orientation = Rotation.from_quat(q)
    seg.imu.gyr[seg.imu.times < 5.0] = 0.0
    step = np.degrees((seg.reference.orientation[999].inv() * seg.reference.orientation[1000]).magnitude()) * 200
    assert step > 7649
    t, g, w = M.averaged_rate_pairs(seg, 'segment_to_world', M.RATE_SPAN_S)
    assert np.isnan(w[np.abs(t - 5.0) < M.RATE_SPAN_S / 2 - 0.01]).all()
    p = M.window_fit(seg, 4.0)
    assert _error_deg(p['fit']) < min(2.0, p['uncertainty_deg'])
