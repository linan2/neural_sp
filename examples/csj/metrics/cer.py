#! /usr/bin/env python
# -*- coding: utf-8 -*-

"""Define evaluation method by Character Error Rate (CSJ corpus)."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import re
from tqdm import tqdm

from utils.io.labels.character import Idx2char
from utils.evaluation.edit_distance import compute_cer


def do_eval_cer(model, model_type, dataset, label_type, data_size, beam_width,
                max_decode_len, eval_batch_size=None,
                progressbar=False):
    """Evaluate trained model by Character Error Rate.
    Args:
        model: the model to evaluate
        model_type (string): ctc or attention or hierarchical_ctc or
            hierarchical_attention or nested_attention
        dataset: An instance of a `Dataset' class
        label_type (string): kanji or kanji or kanji_divide or kana_divide
        data_size (string): fullset or subset
        beam_width: (int): the size of beam
        max_decode_len (int): the length of output sequences
            to stop prediction when EOS token have not been emitted.
            This is used for seq2seq models.
        eval_batch_size (int, optional): the batch size when evaluating the model
        progressbar (bool, optional): if True, visualize the progressbar
    Returns:
        cer_mean (float): An average of CER
    """
    # Reset data counter
    dataset.reset()

    # Set batch size in the evaluation
    if eval_batch_size is not None:
        dataset._batch_size = eval_batch_size

    idx2char = Idx2char(
        vocab_file_path='../metrics/vocab_files/' +
        label_type + '_' + data_size + '.txt')

    cer_mean = 0
    if progressbar:
        pbar = tqdm(total=len(dataset))
    for batch, is_new_epoch in dataset:

        # Decode
        if model_type in ['ctc', 'attention']:
            inputs, labels, inputs_seq_len, labels_seq_len, _ = batch
            is_sub_task = False
        elif model_type in ['hierarchical_ctc', 'hierarchical_attention', 'nested_attention']:
            inputs, _, labels, inputs_seq_len, _, labels_seq_len, _ = batch
            is_sub_task = True
        labels_hyp = model.decode(inputs, inputs_seq_len,
                                  beam_width=beam_width,
                                  max_decode_len=max_decode_len,
                                  is_sub_task=is_sub_task)

        for i_batch in range(len(inputs)):

            ##############################
            # Reference
            ##############################
            if dataset.is_test:
                str_ref = labels[i_batch][0]
                # NOTE: transcript is seperated by space('_')
            else:
                # Convert from list of index to string
                if model_type in ['ctc', 'hierarchical_ctc']:
                    str_ref = idx2char(
                        labels[i_batch][:labels_seq_len[i_batch]])
                elif model_type in ['attention', 'hierarchical_attention', 'nested_attention']:
                    str_ref = idx2char(
                        labels[i_batch][1:labels_seq_len[i_batch] - 1])
                    # NOTE: Exclude <SOS> and <EOS>

            ##############################
            # Hypothesis
            ##############################
            str_hyp = idx2char(labels_hyp[i_batch])

            if model_type in ['attention', 'hierarchical_attention', 'nested_attention']:
                str_hyp = str_hyp.split('>')[0]
                # NOTE: Trancate by the first <EOS>

            # Remove garbage labels
            str_ref = re.sub(r'[_NZー・<>]+', '', str_ref)
            str_hyp = re.sub(r'[_NZー・<>]+', '', str_hyp)

            # Compute CER
            cer_mean += compute_cer(ref=str_ref,
                                    hyp=str_hyp,
                                    normalize=True)

            if progressbar:
                pbar.update(1)

        if is_new_epoch:
            break

    if progressbar:
        pbar.close()

    # Register original batch size
    dataset.reset()

    cer_mean /= len(dataset)

    return cer_mean
