"""Trento upper limb (Bonfiglio et al. 2024) read from the authors' own processed products, not from the raw files.
The Trento readers built on the raw files (`bonfiglio`, `bonfiglio_deposited`) stay as they are; these two readers
exist next to them so the authors' final product can be checked and scored the same way. The effect analysis reads
the deposited condition of Trento from `bonfiglio_final_deposited` (the data as a user of the deposit gets them).

Files read, per participant (`Data/subject <n>/`), nothing else:
- `B_dotComplete` (variable `dotComplete`, 20 trials x 5 sensors TRK, RSC, RUA, RLA, RHD): the authors' sorted DOT
  data (Codes/B_sortDotData.m). Each sensor's export cut to the shortest of the five by row index (the sensors'
  recorded start skew of 2-16 samples is ignored by the authors) and its first row, all-zero, removed. Columns
  (1-based, as the MATLAB code): 1 PacketCounter, 2 SampleTimeFine, 3-6 quaternion, 7-9 accelerometer (m/s^2),
  10-12 gyroscope (deg/s), 13-15 magnetometer, 16-18 Euler angles the authors added.
- `30. Results/qGS_Optotrack` (variable `qGS`, 20 trials): the authors' optical segment orientations
  (Codes/C_Optotrack_analyseData.m), anatomical frames from the digitised landmarks: TH thorax, SC scapula, UA1 and
  UA2 humerus (two models), LA forearm, HA hand. Quaternions [w x y z] from rotm2quat(R) with R = [x y z] the
  segment axes in the optical frame, so they map segment to world. Built from A_OptotrackComplete, whose occluded
  marker samples the authors set to 0 and whose markers they low-pass filtered at 3 Hz. Not aligned to the IMU: the
  authors aligned only their joint angles (H_compareDataV2.m, align_signals, missing from the deposit), joint by joint.
  The humerus model per trial follows the authors' choice for their final joint angles (H_compareDataV2.m): UA1 for
  trials 1, 7, 8, 11, 13, 14, 15, 18, 19 and 20, UA2 for the others.
No marker trajectories are carried, so the marker checks (errors 1, 2 and 4) have nothing to read here.

Time: both streams start at 0 s at 120 Hz, exactly as the products give them (no time information is carried).
Pairing: `bonfiglio_final_deposited` pairs IMU trial k with optical trial k, as the authors' products do.
`bonfiglio_final` pairs each IMU trial with the optical trial that the trial-pairing check (error 8, default
neighbour setting) assigns to it on `bonfiglio_final_deposited`; a trial left unpaired has no segments. The
assignment is computed on first use (about a second) and kept in memory.
Segments: trunk, scapula, upper_arm, forearm, hand. Joints scored: upper_arm-forearm and forearm-hand, as the
raw-file Trento reader, so the readers compare joint for joint.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy.io import loadmat
from scipy.spatial.transform import Rotation

from ..recording import ImuStream, Recording, ReferenceStream, Segment
from .base import DATA_ROOT, DatasetLoader

ROOT = DATA_ROOT / 'bonfiglio' / '_extracted' / 'Optotrak vs DOT' / 'Data'
RATE_HZ = 120.0
TRIAL_NAMES = ('n-pose', 'elbow-flexion', 'elbow-pronation', 'wrist-flexion', 'wrist-deviation',
               'shoulder-internal-rotation', 'shoulder-flexion', 'shoulder-abduction', 'scapula-elevation',
               'scapula-protraction', *(f'task-{i}' for i in range(1, 11)))
SEGMENTS = {'trunk': ('TRK', 'TH'), 'scapula': ('RSC', 'SC'), 'upper_arm': ('RUA', None), 'forearm': ('RLA', 'LA'),
            'hand': ('RHD', 'HA')}
HUMERUS_1_TRIALS = {1, 7, 8, 11, 13, 14, 15, 18, 19, 20}  # H_compareDataV2.m: these use UA1, the others UA2
JOINTS = [('upper_arm', 'forearm'), ('forearm', 'hand')]
ACC, GYR = slice(6, 9), slice(9, 12)  # 0-based columns of a dotComplete sensor array


@lru_cache(maxsize=2)
def _subject(root: str, subject: str):
    folder = Path(root) / f'subject {subject}'
    dot = loadmat(folder / 'B_dotComplete', squeeze_me=True, struct_as_record=False)['dotComplete']
    qgs = loadmat(folder / '30. Results' / 'qGS_Optotrack', squeeze_me=True, struct_as_record=False)['qGS']
    return dot, qgs


def _reference(q_wxyz: np.ndarray) -> ReferenceStream:
    q = np.asarray(q_wxyz, float)
    norm = np.linalg.norm(q, axis=1)
    valid = np.isfinite(q).all(1) & (np.abs(norm - 1) < 1e-3)
    q = np.where(valid[:, None], q, [1.0, 0.0, 0.0, 0.0])
    return ReferenceStream(Rotation.from_quat(q[:, [1, 2, 3, 0]]), RATE_HZ, 0.0, valid, 'segment_to_world')


class TrentoFinalDepositedLoader(DatasetLoader):
    """Trento from the authors' products with the authors' pairing (IMU trial k with optical trial k)."""

    name = 'bonfiglio_final_deposited'
    default_root = ROOT

    def trials(self) -> list[str]:
        subjects = sorted((p.name.split()[1] for p in self.root.glob('subject *')), key=int)
        return [f'{s}/t{k:02d}-{n}' for s in subjects for k, n in enumerate(TRIAL_NAMES, 1)]

    def participant_of(self, trial: str) -> str:
        return trial.split('/')[0]

    def session_of(self, trial: str) -> str:
        return self.participant_of(trial)

    @staticmethod
    def _index(trial: str) -> int:
        return int(trial.split('/')[1].split('-')[0][1:])

    def _optical_trial(self, trial: str) -> str | None:
        return trial

    def load(self, trial: str) -> Recording:
        subject, k = self.participant_of(trial), self._index(trial)
        dot, _ = _subject(str(self.root), subject)
        source = self._optical_trial(trial)
        segments = {}
        if source is not None:
            j = self._index(source)
            _, qgs = _subject(str(self.root), subject)
            optical = qgs[j - 1]
            for seg, (sensor, field) in SEGMENTS.items():
                data = np.asarray(getattr(dot[k - 1], sensor), float)
                imu = ImuStream(np.radians(data[:, GYR]), data[:, ACC], RATE_HZ, 0.0)
                field = field or ('UA1' if j in HUMERUS_1_TRIALS else 'UA2')
                segments[seg] = Segment(seg, imu, _reference(getattr(optical, field)))
        return Recording(self.name, trial, segments, joints=list(JOINTS) if segments else [],
                         metadata=dict(source='authors\' products: B_dotComplete, 30. Results/qGS_Optotrack',
                                       optical_trial=source, timing_as_deposited=True,
                                       humerus_model=None if source is None else
                                       ('UA1' if self._index(source) in HUMERUS_1_TRIALS else 'UA2')),
                         participant=subject, session=subject)


class TrentoFinalLoader(TrentoFinalDepositedLoader):
    """Trento from the authors' products, each IMU trial paired with the optical trial the trial-pairing check
    (error 8) assigns to it."""

    name = 'bonfiglio_final'

    def _optical_trial(self, trial: str) -> str | None:
        table = final_pairing(str(self.root))
        if trial not in table:
            raise KeyError(f'{trial}: not in the trial pairing of {self.root}')
        return table[trial]  # None: left unpaired, the trial has no segments


@lru_cache(maxsize=2)
def final_pairing(root: str = str(ROOT)) -> dict:
    """{IMU trial: assigned optical trial or None} of the trial-pairing check (error 8, default neighbour setting) on
    the authors' products as deposited (TrentoFinalDepositedLoader)."""
    from ..errors.e08_trial_pairing import check_dataset
    table = check_dataset(TrentoFinalDepositedLoader(root), 'bonfiglio_final_deposited')
    return {str(r.trial): (r.assigned if isinstance(r.assigned, str) and r.assigned else None)
            for r in table.itertuples()}
