
# Data Export for Druckmannlab

import math

from collections import defaultdict

import numpy as np
import scipy.io as scio

from pipeline import lab
from pipeline import experiment
from pipeline import ephys

'''

Notes:

  - export includes behavior for trials without ephys data. how to handle?

    if exclude, this means trial indices will be non-contiguous w/r/t database
    if include, this means .mat cell arrays will vary by shape and need
    handling locally.

  - Photostim Data (task_stimulation):

    - Experimental data doesn't contain actual start/end/power times;
      Start is captured per trial with power/duration modelled as session
      parameters. This implies that power+off time in export data are
      synthetic.

'''


def mkfilename(insert_key):
    '''
    create a filename for the given insertion key.
    filename will be of the format map-export_h2o_YYYY-MM-DD_SN_PN.mat

    where:

      - h2o: water restriction number
      - YYYY-MM-DD: session recording date
      - SN: session number for this subject
      - PN: probe number for this session

    '''

    fvars = ((lab.WaterRestriction * experiment.Session
              * ephys.ProbeInsertion) & insert_key).fetch1()

    return 'map-export_{}_{}_{}_{}.mat'.format(
        fvars['water_restriction_number'], str(fvars['session_date']),
        fvars['session'], fvars['insertion_number'])


def export_recording(insert_key, filepath=None):
    '''
    Export a 'recording' (probe specific data + related events) to a file.

    Parameters:

      - insert_key: an ephys.ProbeInsertion.primary_key
        currently: {'subject_id', 'session', 'insertion_number'})

      - filepath: an optional output file path string. If not provided,
        files will be created in the current directory using the 'mkfilename'
        function.
    '''

    if not filepath:
        filepath = mkfilename(insert_key)

    print('exporting {} to {}'.format(insert_key, filepath))

    print('fetching spike/behavior data')

    insertion = (ephys.ProbeInsertion.InsertionLocation & insert_key).fetch1()
    units = (ephys.Unit & insert_key).fetch()

    trial_spikes = (ephys.TrialSpikes & insert_key).fetch(order_by='trial asc')

    behav = (experiment.BehaviorTrial & insert_key).fetch(order_by='trial asc')

    trials = behav['trial']

    exports = ['neuron_single_units', 'neuron_unit_info', 'behavior_report',
               'behavior_early_report', 'behavior_lick_times',
               'task_trial_type', 'task_stimulation', 'task_cue_time']

    edata = {k: None for k in exports}

    print('reshaping/processing for export')

    # neuron_single_units
    # -------------------

    # [[u0t0.spikes, ..., u0tN.spikes], ..., [uNt0.spikes, ..., uNtN.spikes]]

    _su = defaultdict(list)

    ts = trial_spikes[['unit', 'trial', 'spike_times']]

    for u, t in ((u, t) for t in trials for u in units['unit']):
        ud = ts[np.logical_and(ts['unit'] == u, ts['trial'] == t)]
        if ud:
            _su[u].append(ud['spike_times'][0])
        else:
            _su[u].append(np.array([]))

    ndarray_object = np.empty((len(_su.keys()), 1), dtype=np.object)
    for idx, i in enumerate(sorted(_su.keys())):
        ndarray_object[idx, 0] = np.array(_su[i], ndmin=2).T

    edata['neuron_single_units'] = ndarray_object

    # neuron_unit_info
    # ----------------
    #
    # [[depth_in_um, cell_type, recording_location] ...]

    dv = insertion['dv_location'] if insertion['dv_location'] else np.nan
    loc = insertion['brain_location_name']

    types = (ephys.UnitCellType & insert_key).fetch()

    _ui = []
    for u in units:
        if u['unit'] in types['unit']:
            typ = types[np.where(types['unit'] == units[0]['unit'])][0]
            _ui.append([u['unit_posy'] + dv, typ['cell_type'], loc])
        else:
            _ui.append([u['unit_posy'] + dv, 'unknown', loc])

    edata['neuron_unit_info'] = np.array(_ui, dtype='O')

    # behavior_report
    # ---------------

    behavior_report_map = {'hit': 1, 'miss': 0, 'ignore': 0}  # XXX: ignore ok?
    edata['behavior_report'] = np.array([
        behavior_report_map[i] for i in behav['outcome']])

    # behavior_early_report
    # ---------------------

    early_report_map = {'early': 1, 'no early': 0}
    edata['behavior_early_report'] = np.array([
        early_report_map[i] for i in behav['early_lick']])

    # behavior_touch_times
    # --------------------

    behavior_touch_times = None  # NOQA no data (see ActionEventType())

    # behavior_lick_times
    # -------------------

    _lt = []
    licks = (experiment.ActionEvent() & insert_key
             & "action_event_type in ('left lick', 'right lick')").fetch()

    for t in trials:

        _lt.append([float(i) for i in   # decimal -> float
                    licks[licks['trial'] == t]['action_event_time']]
                   if t in licks['trial'] else [])

    edata['behavior_lick_times'] = np.array(_lt)

    behavior_whisker_angle = None  # NOQA no data
    behavior_whisker_dist2pol = None  # NOQA no data

    # task_trial_type
    # ---------------

    task_trial_type_map = {'left': 'l', 'right': 'r'}
    edata['task_trial_type'] = np.array([
        task_trial_type_map[i] for i in behav['trial_instruction']], dtype='O')

    # task_stimulation
    # ----------------

    _ts = []  # [[power, type, on-time, off-time], ...]

    photostim = (experiment.Photostim() & insert_key).fetch()

    photostim_map = {}
    photostim_dat = {}
    photostim_keys = ['left_alm', 'right_alm', 'both_alm']
    photostim_vals = [1, 2, 6]

    # XXX: we don't detect duplicate presence of photostim_keys in data
    for fk, rk in zip(photostim_keys, photostim_vals):

        i = np.where(photostim['brain_location_name'] == fk)[0][0]
        j = photostim[i]['photo_stim']
        photostim_map[j] = rk
        photostim_dat[j] = photostim[i]

    photostim_ev = (experiment.PhotostimEvent & insert_key).fetch()

    for t in trials:

        if t in photostim_ev['trial']:

            ev = photostim_ev[np.where(photostim_ev['trial'] == t)]
            ps = photostim_map[ev['photo_stim'][0]]
            pd = photostim_dat[ev['photo_stim'][0]]

            _ts.append([float(ev['power']), ps,
                        float(ev['photostim_event_time']),
                        float(ev['photostim_event_time'] + pd['duration'])])

        else:
            _ts.append([0, math.nan, math.nan, math.nan])

    edata['task_stimulation'] = np.array(_ts)

    # task_pole_time
    # --------------

    task_pole_time = None  # NOQA no data

    # task_cue_time
    # -------------

    _tct = (experiment.TrialEvent()
            & {**insert_key, 'trial_event_type': 'go'}).fetch(
                'trial_event_time')

    edata['task_cue_time'] = np.array([float(i) for i in _tct])

    print('saving to', filepath)

    scio.savemat(filepath, edata)