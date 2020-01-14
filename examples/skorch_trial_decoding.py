import sys
import logging
import importlib

log = logging.getLogger()
log.setLevel('INFO')

logging.basicConfig(format='%(asctime)s %(levelname)s : %(message)s',
                    level=logging.INFO, stream=sys.stdout)

import mne
from mne.io import concatenate_raws

# 5,6,7,10,13,14 are codes for executed and imagined hands/feet
subject_id = 22  # carefully cherry-picked to give nice results on such limited data :)
event_codes = [5, 6, 9, 10, 13, 14]
# event_codes = [3,4,5,6,7,8,9,10,11,12,13,14]

# This will download the files if you don't have them yet,
# and then return the paths to the files.
physionet_paths = mne.datasets.eegbci.load_data(subject_id, event_codes)

# Load each of the files
parts = [mne.io.read_raw_edf(path, preload=True, stim_channel='auto',
                             verbose='WARNING')
         for path in physionet_paths]

# Concatenate them
raw = concatenate_raws(parts)

# Find the events in this dataset
events, _ = mne.events_from_annotations(raw)

# Use only EEG channels
eeg_channel_inds = mne.pick_types(raw.info, meg=False, eeg=True, stim=False,
                                  eog=False,
                                  exclude='bads')

# Extract trials, only using EEG channels
epoched = mne.Epochs(raw, events, dict(hands_or_left=2, feet_or_right=3),
                     tmin=1, tmax=4.1, proj=False, picks=eeg_channel_inds,
                     baseline=None, preload=True)

import numpy as np

X = (epoched.get_data() * 1e6).astype(np.float32)
y = (epoched.events[:, 2] - 2).astype(np.int64)  # 2,3 -> 0,1

from braindecode.datautil.signal_target import SignalAndTarget

# in skorch flow it is also a validation set (train-valid split set in skorch model)
# To not shuffle data for train-valid split we need to create a splitter
train_set = SignalAndTarget(X[:70], y=y[:70])

from braindecode.models import ShallowFBCSPNet
from braindecode.torch_ext.util import \
    set_random_seeds  # XXX : move to braindecode.util

# Set if you want to use GPU
# You can also use torch.cuda.is_available() to determine if cuda is available on your machine.
cuda = False
set_random_seeds(seed=20170629, cuda=cuda)
n_classes = 2
in_chans = train_set.X.shape[1]

from skorch.classifier import NeuralNet, NeuralNetClassifier
from skorch.dataset import CVSplit
from torch.utils.data import Dataset


class EEGDataSet(Dataset):
    def __init__(self, X, y):
        self.X = X
        if self.X.ndim == 3:
            self.X = self.X[:, :, :, None]
        self.y = y

    def __len__(self):
        return len(self.X)

    def __getitem__(self, idx):
        return self.X[idx], self.y[idx]


train_set = EEGDataSet(train_set.X, train_set.y)

from torch import optim
import torch
import torch.nn.functional as F


class TrainTestSplit(object):
    def __init__(self, train_size):
        assert isinstance(train_size, (int, float))
        self.train_size = train_size

    def __call__(self, dataset, y, **kwargs):
        # can we directly use this https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.train_test_split.html
        # or stick to same API
        if isinstance(self.train_size, int):
            dataset_train = EEGDataSet(dataset.X[:self.train_size],
                                       dataset.y[:self.train_size])
            dataset_valid = EEGDataSet(dataset.X[self.train_size:],
                                       dataset.y[self.train_size:])
        elif isinstance(self.train_size, float):
            raise Exception
            dataset_train = torch.utils.data.Subset(dataset, range(
                0, int(self.train_size * len(dataset))))
            dataset_valid = torch.utils.data.Subset(dataset, range(
                int(self.train_size * len(dataset)), len(X)))
        return dataset_train, dataset_valid


from braindecode.skorch_training.skorchmodels import BraindecodeClassifier
from skorch.callbacks import EpochScoring

set_random_seeds(20200114, True)

# final_conv_length = auto ensures we only get a single output in the time dimension
model = ShallowFBCSPNet(in_chans=in_chans, n_classes=n_classes,
                        input_time_length=train_set.X.shape[2],
                        final_conv_length='auto').create_network()
if cuda:
    model.cuda()

# It can use also NeuralNetClassifier
skorch_model = BraindecodeClassifier(model,
                                     criterion=torch.nn.NLLLoss,
                                     optimizer=optim.AdamW,
                                     train_split=TrainTestSplit(train_size=40),
                                     optimizer__lr=0.0625 * 0.01,
                                     optimizer__weight_decay=0, batch_size=64,
                                     callbacks=[('train_accuracy',
                                                 EpochScoring('accuracy',
                                                              on_train=True,
                                                              lower_is_better=False,
                                                              name='train_acc'))])
skorch_model.fit(train_set, y=None, epochs=4)

test_set = EEGDataSet(X[70:], y=y[70:])

skorch_model.evaluate(test_set.X, test_set.y)

skorch_model.evaluate(test_set)

skorch_model.predict(test_set)

skorch_model.predict_proba(test_set)
