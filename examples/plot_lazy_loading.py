"""Comparing eager and lazy loading
===================================

In this example, we compare the execution time and memory requirements of 1)
eager loading, i.e., preloading the entire data into memory and 2) lazy loaging,
i.e., only loading examples from disk when they are required.

While eager loading might be required for some preprocessing steps to be carried
out on continuous data (e.g., temporal filtering), it also allows fast access to
the data during training. However, this might come at the expense of large
memory usage, and can ultimately become impossible to do if the dataset does not
fit into memory (e.g., the TUH EEG dataset's >1,5 TB of recordings will not fit
in the memory of most machines).

Lazy loading avoids this potential memory issue by loading examples from disk
when they are required. This means large datasets can be used for training,
however this introduces some file-reading overhead every time an example must
be extracted. Some preprocessing steps that require continuous data also cannot
be applied as they normally would.

The following compares eager and lazy loading in a realistic scenario and shows
that...

For lazy loading to be possible, files must be saved in an MNE-compatible format
such as 'fif', 'edf', etc.
-> MOABB datasets are usually preloaded already?


Steps:
-> Initialize simple model
-> For loading in ('eager', 'lazy'):
    a) Load BNCI dataset with preload=True or False
    b) Apply raw transform (either eager, or keep it for later)
    b) Apply windower (either eager, or keep it for later)
    c) Add window transform (either eager, or keep it for later)
    d) Train for 10 epochs
-> Measure
    -> Total running time
    -> Time per batch
    -> Max and min memory consumption (or graph across time?)
    -> CPU/GPU usage across time


TODO:
- Automate the getting of TUH
- Cast data to float, targets to long in Dataset itself
    -> Should the conversion to torch.Tensor be made explicitly in the
       dataset class?

"""

# Authors: Hubert Banville <hubert.jbanville@gmail.com>
#
# License: BSD (3-clause)

from collections import OrderedDict
import time

import torch
from torch import nn, optim
from torch.utils.data import DataLoader

import mne
import numpy as np
import matplotlib.pyplot as plt
from IPython.display import display

from braindecode.datasets import TUHAbnormal
from braindecode.datautil.windowers import create_fixed_length_windows
from braindecode.datautil.transforms import transform_concat_ds
from braindecode.models import ShallowFBCSPNet


mne.set_log_level('WARNING')  # without this, a message will be printed everytime a window is extracted

# ============
# Lazy loading
# ============

##############################################################################
# Lazy loading
# -------------
# We load the same three subjects as above, but this time we specify that the
# data should not be preloaded.
path = '/storage/store/data/tuh_eeg/www.isip.piconepress.com/projects/tuh_eeg/downloads/tuh_eeg_abnormal/v2.0.0/edf/'
subject_ids = [0, 1, 2]
ds = TUHAbnormal(
    path, subject_ids=subject_ids, target_name="pathological", preload=False)

# Let's check whether the data is preloaded
print(ds.datasets[0].raw.preload)

##############################################################################
# As opposed to the eager loading case, it is not possible to apply transforms
# that would require loading the continuous data into memory. All transform
# methods have to be applied on-the-fly.

###############################################################################
# As above, we create evenly spaced 4-s windows:

fs = ds.datasets[0].raw.info['sfreq']

window_len_samples = int(fs * 4)
windows_ds = create_fixed_length_windows(
    ds, start_offset_samples=0, stop_offset_samples=None,
    supercrop_size_samples=window_len_samples, 
    supercrop_stride_samples=window_len_samples, drop_samples=True, 
    preload=False)

# print(len(windows_ds))
# for x, y, supercrop_ind in windows_ds:
#     print(x.shape, y, supercrop_ind)
#     break

# Let's check whether the data is preloaded
print(windows_ds.datasets[0].windows.preload)

###############################################################################
# We apply an additional filtering step, but this time on the windowed data.
# THERE IS NO WAY TO INCLUDE ON-THE-FLY TRANSFORMS CURRENTLY.

windows_transform_dict = OrderedDict({
    'filter': {
        'l_freq': 10, 
        'h_freq': 20, 
        'picks': ['eeg']  # This controls which channels are filtered, but it keeps all of them.
    }
})

# transform_concat_ds(windows_ds, windows_transform_dict)  # THIS LOADS THE DATA

# ###############################################################################
# Before using a WindowsDataset, we must call `drop_bad` so that bad epochs
# can be identified.
# XXX: Could this step be performed with `transform_concat_ds` instead?
for win_ds in windows_ds.datasets:
    win_ds.windows.drop_bad()

# Let's check whether the data is preloaded one last time:
print(windows_ds.datasets[0].windows.preload)

###############################################################################
# We now have a lazy-loaded dataset. We can use it to train a neural network.

use_cuda = False
n_epochs = 1

# Define data loader
dataloader = DataLoader(
    windows_ds, batch_size=128, shuffle=False, sampler=None, batch_sampler=None, 
    num_workers=0, collate_fn=None, pin_memory=False, drop_last=False, 
    timeout=0, worker_init_fn=None)

# Instantiate model and optimizer
n_channels = len(windows_ds.datasets[0].windows.ch_names)
n_classes = 2
model = ShallowFBCSPNet(
    n_channels, n_classes, input_time_length=window_len_samples, 
    n_filters_time=40, filter_time_length=25, n_filters_spat=40, 
    pool_time_length=75, pool_time_stride=15, final_conv_length=30, 
    split_first_layer=True, batch_norm=True, batch_norm_alpha=0.1, 
    drop_prob=0.5)

optimizer = optim.Adam(model.parameters())
if use_cuda:
    model.cuda()
    X, y = X.cuda(), y.cuda()
loss = nn.CrossEntropyLoss()

# Train model on fake data
for _ in range(n_epochs):
    for X, y, _  in dataloader:
        model.train()
        model.zero_grad()

        y_hat = torch.sum(model(X.float()), axis=-1)
        loss_val = loss(y_hat, y.long())
        print(loss_val)

        loss_val.backward()
        optimizer.step()

start = time.time()
duration = (time.time() - start) * 1e3 / n_minibatches  # in ms

print(f'Took {duration} ms per minibatch.')
