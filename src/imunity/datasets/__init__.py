"""Dataset loaders: one loader per dataset, each reading one deposit format and returning `Recording` objects.

    from imunity.datasets import load_dataset
    loader = load_dataset('dfjimu')
    rec = loader.load(loader.trials()[0])

Short name          Dataset                                                Loader class
tuk6min             TUK six-minute walk                                    TukLoader (tuk.py)
opensense           OpenSense (Al Borno 2022)                              OpensenseLoader
fullbody17          Hankyong full body, 17 Xsens IMUs                      Fullbody17Loader
camargo             Camargo lower limb (Georgia Tech), treadmill part      CamargoLoader
wmcg                Central Florida walking (WMCG)                         WmcgLoader
compliant_terrain   Delaware compliant terrain                             CompliantTerrainLoader
hip_rom_y           Yanshan hip range of motion (Hip-ROM-Y)                HipRomYLoader
is2b                IS2B cadaver knee                                      Is2BLoader
grouvel             Geneva gait (Grouvel 2022)                             GrouvelLoader
bonfiglio           Trento upper limb (Bonfiglio 2024)                     BonfiglioLoader
grimmer_stairs      Darmstadt stairs (Grimmer)                             GrimmerStairsLoader
ultramocap          Central Florida upper limb (ULTRA-MoCap)               UltramocapLoader
diodem              DIODEM mechanical chains (Bachhuber 2025)              DiodemLoader (diodem.py)
dfjimu              dfjimu ABB IRB120 robot arm                            DfjimuLoader (dfjimu.py)
mfimt_d01           mfimt 3D-printed chain, recording d01                  MfimtLoader (mfimt.py)
reemc               REEM-C humanoid robot                                  ReemcLoader
openaxes            OpenAXES UR5 robot arm                                 OpenaxesLoader
repoimu             RepoIMU triple pendulum                                RepoimuLoader

Helper readers (not datasets of their own):
grouvel_raw         Geneva gait, IMU rebuilt from the raw session logs     GrouvelRawLoader (grouvel_raw.py)
bonfiglio_deposited Trento upper limb with the deposit's own pairing       BonfiglioDepositedLoader
bonfiglio_final_deposited  Trento from the authors' processed products,    TrentoFinalDepositedLoader (trento_final.py)
                    with their pairing
bonfiglio_final     Trento from the authors' processed products,           TrentoFinalLoader (trento_final.py)
                    re-paired by the trial-pairing check (error 8)

Loaders without a file named are built in _bridge.py on the dataset readers in `readers/`.

Dataset lists: PAPER_DATASETS are the 15 datasets the paper reports; EXTRA_DATASETS the three that have loaders, a
download and a saved split and run through every check, correction and the effect analysis, but are not reported in
the paper (mfimt_d01, openaxes, repoimu); ALL_DATASETS both, 18, in the order above. download('all'), the error
handlers and the effect analysis default to all 18; pass PAPER_DATASETS for the paper's set.
"""
from .base import DatasetLoader
from .dfjimu import DfjimuLoader
from .grouvel_raw import GrouvelRawLoader
from .diodem import DiodemLoader
from ._bridge import READER_LOADERS, ReaderLoader
from .mfimt import MfimtLoader
from .trento_final import TrentoFinalDepositedLoader, TrentoFinalLoader
from .tuk import TukLoader

LOADERS: dict[str, type[DatasetLoader]] = {
    loader.name: loader for loader in (DfjimuLoader, MfimtLoader, TukLoader, DiodemLoader,
                                       *READER_LOADERS, GrouvelRawLoader, TrentoFinalDepositedLoader,
                                       TrentoFinalLoader)
}

HELPER_READERS: dict[str, str] = {
    'grouvel_raw': 'Geneva gait with the IMU streams rebuilt from the raw session logs (mis-assembled streams, '
                   'error 10)',
    'bonfiglio_deposited': "Trento upper limb with the deposit's own trial pairing, read for the trial-pairing "
                           'check (error 8)',
    'bonfiglio_final_deposited': "Trento upper limb read from the authors' processed products (B_dotComplete, "
                                 'qGS_Optotrack) with their pairing',
    'bonfiglio_final': "Trento upper limb read from the authors' processed products, re-paired by the trial-pairing "
                       'check (error 8)',
}
ALL_DATASETS: list[str] = ['tuk6min', 'opensense', 'fullbody17', 'camargo', 'wmcg', 'compliant_terrain',
                           'hip_rom_y', 'is2b', 'grouvel', 'bonfiglio', 'grimmer_stairs', 'ultramocap', 'diodem',
                           'dfjimu', 'mfimt_d01', 'reemc', 'openaxes', 'repoimu']
# Not reported in the paper; everything else works for them as for the paper's datasets.
EXTRA_DATASETS: list[str] = ['mfimt_d01', 'openaxes', 'repoimu']
PAPER_DATASETS: list[str] = [d for d in ALL_DATASETS if d not in EXTRA_DATASETS]
assert set(LOADERS) == set(ALL_DATASETS) | set(HELPER_READERS)


def load_dataset(name: str, **options) -> DatasetLoader:
    """The loader for a dataset by its short name; options go to the loader (e.g. root=..., imu='nonrigid')."""
    if name not in LOADERS:
        raise KeyError(f'No loader for {name!r}. Available: {", ".join(LOADERS)}')
    return LOADERS[name](**options)


__all__ = ['DatasetLoader', 'LOADERS', 'ALL_DATASETS', 'PAPER_DATASETS', 'EXTRA_DATASETS', 'HELPER_READERS',
           'load_dataset', 'DfjimuLoader', 'GrouvelRawLoader', 'MfimtLoader', 'TukLoader', 'DiodemLoader',
           'ReaderLoader', 'TrentoFinalDepositedLoader', 'TrentoFinalLoader']
