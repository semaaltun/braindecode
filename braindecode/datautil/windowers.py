"""Get epochs from mne.Raw
"""

# Authors: Hubert Banville <hubert.jbanville@gmail.com>
#          Lukas Gemein <l.gemein@gmail.com>
#          Simon Brandt <simonbrandt@protonmail.com>
#          David Sabbagh <dav.sabbagh@gmail.com>
#
# License: BSD (3-clause)

import numpy as np
import mne
import pandas as pd


def create_windows_from_events(
        base_ds, trial_start_offset_samples, trial_stop_offset_samples,
        supercrop_size_samples, supercrop_stride_samples, drop_samples,
        mapping=None):
    """A Windower that creates supercrops/windows based on events in mne.Raw.
    Therefore, it fits supercrops of supercrop_size_samples in
    trial_start_offset_samples to trial_stop_offset_samples separated by
    supercrop_stride_samples. If the last supercrop does not end
    at trial_stop_offset_samples, creates another overlapping supercrop that
    ends at trial_stop_offset_samples if drop_samples is set to False.

    in mne: tmin (s)                    trial onset        onset + duration (s)
    trial:  |--------------------------------|--------------------------------|
    here:   trial_start_offset_samples                trial_stop_offset_samples

    Parameters
    ----------
    trial_start_offset_samples: int
        start offset from original trial onsets in samples
    trial_stop_offset_samples: int
        stop offset from original trial onsets in samples
    supercrop_size_samples: int
        supercrop size
    supercrop_stride_samples: int
        stride between supercrops
    drop_samples: bool
        whether or not have a last overlapping supercrop/window, when
        supercrops/windows do not equally devide the continuous signal
    mapping: dict(str: int)
        mapping from event description to target value
    """
    _check_windowing_arguments(
        trial_start_offset_samples, trial_stop_offset_samples,
        supercrop_size_samples, supercrop_stride_samples)

    events = mne.find_events(base_ds.raw)
    onsets = events[:, 0]
    description = events[:, -1]
    i_trials, i_supercrop_in_trials, starts, stops = _compute_supercrop_inds(
        onsets, trial_start_offset_samples, trial_stop_offset_samples,
        supercrop_size_samples, supercrop_stride_samples, drop_samples)
    events = [[start, supercrop_size_samples, description[i_trials[i_start]]]
              for i_start, start in enumerate(starts)]
    events = np.array(events)
    assert (np.diff(events[:,0]) > 0).all(), (
        "trials overlap not implemented")
    description = events[:, -1]
    if mapping is not None:
        # Apply remapping of targets
        description = np.array([mapping[d] for d in description])
        events[:, -1] = description

    metadata = pd.DataFrame(
        zip(i_supercrop_in_trials, starts, stops, description),
        columns=["i_supercrop_in_trial", "i_start_in_trial",
                 "i_stop_in_trial", "target"])

    # supercrop size - 1, since tmax is inclusive
    return mne.Epochs(
        base_ds.raw, events, baseline=None, tmin=0,
        tmax=(supercrop_size_samples - 1) / base_ds.raw.info["sfreq"],
        metadata=metadata)


def create_fixed_length_windows(
        base_ds, trial_start_offset_samples, trial_stop_offset_samples,
        supercrop_size_samples, supercrop_stride_samples, drop_samples,
        mapping=None):
    """A Windower that creates supercrops/windows based on fake events that
    equally divide the continuous signal.

    Parameters
    ----------
    base_ds: BaseDataset
        a base dataset holding raw and descpription
    trial_start_offset_samples: int
        start offset from original trial onsets in samples
    trial_stop_offset_samples: int
        stop offset from original trial onsets in samples
    supercrop_size_samples: int
        supercrop size
    supercrop_stride_samples: int
        stride between supercrops
    drop_samples: bool
        whether or not have a last overlapping supercrop/window, when
        supercrops/windows do not equally devide the continuous signal
    mapping: dict(str: int)
        mapping from event description to target value
    """
    _check_windowing_arguments(
        trial_start_offset_samples, trial_stop_offset_samples,
        supercrop_size_samples, supercrop_stride_samples)

    # already includes last incomplete supercrop start
    stop = (base_ds.raw.n_times
            if trial_stop_offset_samples is None
            else trial_stop_offset_samples)
    starts = np.arange(
        trial_start_offset_samples, stop, supercrop_stride_samples)
    if drop_samples:
        starts = starts[:-1]
    else:
        # if last supercrop does not end at trial stop, make it stop
        # there
        if starts[-1] != base_ds.raw.n_times - supercrop_size_samples:
            starts[-1] = base_ds.raw.n_times - supercrop_size_samples

    # TODO: handle multi-target case / non-integer target case
    assert len(base_ds.info[base_ds.target]) == 1, (
        "multi-target not supported")
    description = base_ds.info[base_ds.target].iloc[0]
    # https://github.com/numpy/numpy/issues/2951
    if not isinstance(description, np.integer):
        assert mapping is not None, (
            f"a mapping from '{description}' to int is required")
        description = mapping[description]
    events = [[start, supercrop_size_samples, description]
              for i_start, start in enumerate(starts)]
    metadata = pd.DataFrame(
        zip(np.arange(len(events)), starts, starts + supercrop_size_samples,
            len(events) *[description]),
        columns=["i_supercrop_in_trial", "i_start_in_trial",
                 "i_stop_in_trial", "target"])
    # supercrop size - 1, since tmax is inclusive
    return mne.Epochs(
        base_ds.raw, events, baseline=None,
        tmin=0, tmax=(supercrop_size_samples - 1) / base_ds.raw.info["sfreq"],
        metadata=metadata)


def _compute_supercrop_inds(
        onsets, start_offset, stop_offset, size, stride, drop_samples):
    """Create supercrop starts from trial onsets (shifted by offset) to trial
    end separated by stride as long as supercrop size fits into trial

    Parameters
    ----------
    onsets: array-like
        trial onsets in samples
    start_offset: int
        start offset from original trial onsets in samples
    stop_offset: int
        stop offset from original trial onsets in samples
    size: int
        supercrop size
    stride: int
        stride between supercrops
    drop_samples: bool
        toggles of shifting last supercrop within range or dropping last samples

    Returns (list, list, list, list)
    -------
        trial, i_supercrop_in_trial, start sample and stop sample of supercrops
    """
    # trial ends are defined by trial starts (onsets maybe shifted by offset)
    # and end
    stops = onsets + stop_offset
    i_supercrop_in_trials, i_trials, starts = [], [], []
    for onset_i, onset in enumerate(onsets):
        # between original trial onsets (shifted by start_offset) and stops,
        # generate possible supercrop starts with given stride
        possible_starts = np.arange(
            onset+start_offset, onset+stop_offset, stride)

        # possible supercrop start is actually a start, if supercrop size fits
        # in trial start and stop
        for i_supercrop, s in enumerate(possible_starts):
            if (s + size) <= stops[onset_i]:
                starts.append(s)
                i_supercrop_in_trials.append(i_supercrop)
                i_trials.append(onset_i)

        # if the last supercrop start + supercrop size is not the same as
        # onset + stop_offset, create another supercrop that overlaps and stops
        # at onset + stop_offset
        if not drop_samples:
            if starts[-1] + size != onset + stop_offset:
                starts.append(onset + stop_offset - size)
                i_supercrop_in_trials.append(i_supercrop_in_trials[-1] + 1)
                i_trials.append(onset_i)

    # update stops to now be event stops instead of trial stops
    stops = np.array(starts) + size
    assert len(i_supercrop_in_trials) == len(starts) == len(stops)
    return i_trials, i_supercrop_in_trials, starts, stops


def _check_windowing_arguments(
        trial_start_offset_samples, trial_stop_offset_samples,
        supercrop_size_samples, supercrop_stride_samples):
    assert supercrop_size_samples > 0, (
        "supercrop size has to be larger than 0")
    assert supercrop_stride_samples > 0, (
        "supercrop stride has to be larger than 0")
    # TODO: assert values are integers
    # TODO: assert start < stop