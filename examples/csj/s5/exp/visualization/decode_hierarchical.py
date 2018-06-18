#! /usr/bin/env python
# -*- coding: utf-8 -*-

"""Generate texts by the hierarchical ASR model (CSJ corpus)."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from os.path import join, abspath
import sys
import argparse
import re
from distutils.util import strtobool

sys.path.append(abspath('../../../'))
from models.load_model import load
from examples.csj.s5.exp.dataset.load_dataset_hierarchical import Dataset
from utils.io.labels.word import Word2char
from utils.config import load_config
from utils.evaluation.edit_distance import compute_wer
from utils.evaluation.resolving_unk import resolve_unk

parser = argparse.ArgumentParser()
parser.add_argument('--data_save_path', type=str,
                    help='path to saved data')
parser.add_argument('--model_path', type=str,
                    help='path to the model to evaluate')
parser.add_argument('--epoch', type=int, default=-1,
                    help='the epoch to restore')
parser.add_argument('--eval_batch_size', type=int, default=1,
                    help='the size of mini-batch in evaluation')
parser.add_argument('--beam_width', type=int, default=1,
                    help='the size of beam in the main task')
parser.add_argument('--beam_width_sub', type=int, default=1,
                    help='the size of beam in the sub task')
parser.add_argument('--length_penalty', type=float, default=0,
                    help='length penalty')
parser.add_argument('--coverage_penalty', type=float, default=0,
                    help='coverage penalty')
parser.add_argument('--rnnlm_weight', type=float, default=0,
                    help='the weight of RNNLM score of the main task')
parser.add_argument('--rnnlm_weight_sub', type=float, default=0,
                    help='the weight of RNNLM score of the sub task')
parser.add_argument('--rnnlm_path', default=None, type=str,  nargs='?',
                    help='path to the RMMLM of the main task')
parser.add_argument('--rnnlm_path_sub', default=None, type=str, nargs='?',
                    help='path to the RMMLM of the sub task')
parser.add_argument('--resolving_unk', type=strtobool, default=False)
parser.add_argument('--a2c_oracle', type=strtobool, default=False)
parser.add_argument('--joint_decoding', type=strtobool, default=False)
parser.add_argument('--score_sub_weight', type=float, default=0)

MAX_DECODE_LEN_WORD = 100
MIN_DECODE_LEN_WORD = 1
MAX_DECODE_LEN_CHAR = 200
MIN_DECODE_LEN_CHAR = 1


def main():

    args = parser.parse_args()

    # Load a ASR config file
    config = load_config(join(args.model_path, 'config.yml'), is_eval=True)

    # Load dataset
    dataset = Dataset(data_save_path=args.data_save_path,
                      input_freq=config['input_freq'],
                      use_delta=config['use_delta'],
                      use_double_delta=config['use_double_delta'],
                      data_type='eval1',
                      # data_type='eval2',
                      # data_type='eval3',
                      data_size=config['data_size'],
                      label_type=config['label_type'],
                      label_type_sub=config['label_type_sub'],
                      batch_size=args.eval_batch_size,
                      sort_utt=False, reverse=False, tool=config['tool'])
    config['num_classes'] = dataset.num_classes
    config['num_classes_sub'] = dataset.num_classes_sub

    # For cold fusion
    if config['rnnlm_fusion_type'] and config['rnnlm_path']:
        # Load a RNNLM config file
        config['rnnlm_config'] = load_config(
            join(args.model_path, 'config_rnnlm.yml'))
        assert config['label_type'] == config['rnnlm_config']['label_type']
        assert args.rnnlm_weight > 0
        config['rnnlm_config']['num_classes'] = dataset.num_classes
    else:
        config['rnnlm_config'] = None

    if config['rnnlm_fusion_type'] and config['rnnlm_path_sub']:
        # Load a RNNLM config file
        config['rnnlm_config_sub'] = load_config(
            join(args.model_path, 'config_rnnlm_sub.yml'))
        assert config['label_type_sub'] == config['rnnlm_config_sub']['label_type']
        assert args.rnnlm_weight_sub > 0
        config['rnnlm_config_sub']['num_classes'] = dataset.num_classes_sub
    else:
        config['rnnlm_config_sub'] = None

    # Load the ASR model
    model = load(model_type=config['model_type'],
                 config=config,
                 backend=config['backend'])

    # Restore the saved parameters
    model.load_checkpoint(save_path=args.model_path, epoch=args.epoch)

    # For shallow fusion
    if not (config['rnnlm_fusion_type'] and config['rnnlm_path']) and args.rnnlm_path is not None and args.rnnlm_weight > 0:
        # Load a RNNLM config file
        config_rnnlm = load_config(
            join(args.rnnlm_path, 'config.yml'), is_eval=True)
        assert config['label_type'] == config_rnnlm['label_type']
        config_rnnlm['num_classes'] = dataset.num_classes

        # Load the pre-trianed RNNLM
        rnnlm = load(model_type=config_rnnlm['model_type'],
                     config=config_rnnlm,
                     backend=config_rnnlm['backend'])
        rnnlm.load_checkpoint(save_path=args.rnnlm_path, epoch=-1)
        rnnlm.rnn.flatten_parameters()
        model.rnnlm_0_fwd = rnnlm

    if not (config['rnnlm_fusion_type'] and config['rnnlm_path_sub']) and args.rnnlm_path_sub is not None and args.rnnlm_weight_sub > 0:
        # Load a RNNLM config file
        config_rnnlm_sub = load_config(
            join(args.rnnlm_path_sub, 'config.yml'), is_eval=True)
        assert config['label_type_sub'] == config_rnnlm_sub['label_type']
        config_rnnlm_sub['num_classes'] = dataset.num_classes_sub

        # Load the pre-trianed RNNLM
        rnnlm_sub = load(model_type=config_rnnlm_sub['model_type'],
                         config=config_rnnlm_sub,
                         backend=config_rnnlm_sub['backend'])
        rnnlm_sub.load_checkpoint(save_path=args.rnnlm_path_sub, epoch=-1)
        rnnlm_sub.rnn.flatten_parameters()
        model.rnnlm_1_fwd = rnnlm_sub

    # GPU setting
    model.set_cuda(deterministic=False, benchmark=True)

    sys.stdout = open(join(args.model_path, 'decode.txt'), 'w')

    word2char = Word2char(dataset.vocab_file_path,
                          dataset.vocab_file_path_sub)

    for batch, is_new_epoch in dataset:
        # Decode
        if model.model_type == 'nested_attention':
            if args.a2c_oracle:
                if dataset.is_test:
                    max_label_num = 0
                    for b in range(len(batch['xs'])):
                        if max_label_num < len(list(batch['ys_sub'][b])):
                            max_label_num = len(list(batch['ys_sub'][b]))

                    ys_sub = []
                    for b in range(len(batch['xs'])):
                        indices = dataset.char2idx(batch['ys_sub'][b])
                        ys_sub += [indices]
                        # NOTE: transcript is seperated by space('_')
            else:
                ys_sub = batch['ys_sub']

            best_hyps, aw, best_hyps_sub, aw_sub, _, perm_idx = model.decode(
                batch['xs'],
                beam_width=args.beam_width,
                max_decode_len=MAX_DECODE_LEN_WORD,
                min_decode_len=MIN_DECODE_LEN_WORD,
                beam_width_sub=args.beam_width_sub,
                max_decode_len_sub=MAX_DECODE_LEN_CHAR,
                min_decode_len_sub=MIN_DECODE_LEN_CHAR,
                length_penalty=args.length_penalty,
                coverage_penalty=args.coverage_penalty,
                rnnlm_weight=args.rnnlm_weight,
                rnnlm_weight_sub=args.rnnlm_weight_sub,
                teacher_forcing=args.a2c_oracle,
                ys_sub=ys_sub)
        else:
            best_hyps, aw, perm_idx = model.decode(
                batch['xs'],
                beam_width=args.beam_width,
                max_decode_len=MAX_DECODE_LEN_WORD,
                min_decode_len=MIN_DECODE_LEN_WORD,
                length_penalty=args.length_penalty,
                coverage_penalty=args.coverage_penalty,
                rnnlm_weight=args.rnnlm_weight)
            best_hyps_sub, aw_sub, _ = model.decode(
                batch['xs'],
                beam_width=args.beam_width_sub,
                max_decode_len=MAX_DECODE_LEN_CHAR,
                min_decode_len=MIN_DECODE_LEN_CHAR,
                length_penalty=args.length_penalty,
                coverage_penalty=args.coverage_penalty,
                rnnlm_weight=args.rnnlm_weight_sub,
                task_index=1)

        if model.model_type == 'hierarchical_attention' and args.joint_decoding is not None:
            best_hyps_joint, aw_joint, best_hyps_sub_joint, aw_sub_joint, _ = model.decode(
                batch['xs'],
                beam_width=args.beam_width,
                max_decode_len=MAX_DECODE_LEN_WORD,
                min_decode_len=MIN_DECODE_LEN_WORD,
                length_penalty=args.length_penalty,
                coverage_penalty=args.coverage_penalty,
                rnnlm_weight=args.rnnlm_weight,
                rnnlm_weight_sub=args.rnnlm_weight_sub,
                joint_decoding=args.joint_decoding,
                space_index=dataset.char2idx('_')[0],
                oov_index=dataset.word2idx('OOV')[0],
                word2char=word2char,
                idx2word=dataset.idx2word,
                idx2char=dataset.idx2char,
                score_sub_weight=args.score_sub_weight)

        ys = [batch['ys'][i] for i in perm_idx]
        ys_sub = [batch['ys_sub'][i] for i in perm_idx]

        for b in range(len(batch['xs'])):
            # Reference
            if dataset.is_test:
                str_ref = ys[b]
                str_ref_sub = ys_sub[b]
                # NOTE: transcript is seperated by space('_')
            else:
                str_ref = dataset.idx2word(ys[b])
                str_ref_sub = dataset.idx2char(ys_sub[b])

            # Hypothesis
            str_hyp = dataset.idx2word(best_hyps[b])
            str_hyp_sub = dataset.idx2char(best_hyps_sub[b])
            if model.model_type == 'hierarchical_attention' and args.joint_decoding is not None:
                str_hyp_joint = dataset.idx2word(best_hyps_joint[b])
                str_hyp_sub_joint = dataset.idx2char(best_hyps_sub_joint[b])

            # Resolving UNK
            if 'OOV' in str_hyp and args.resolving_unk:
                str_hyp_no_unk = resolve_unk(
                    str_hyp, best_hyps_sub[b], aw[b], aw_sub[b], dataset.idx2char)
            if model.model_type == 'hierarchical_attention' and args.joint_decoding is not None:
                if 'OOV' in str_hyp_joint and args.resolving_unk:
                    str_hyp_no_unk_joint = resolve_unk(
                        str_hyp_joint, best_hyps_sub_joint[b], aw_joint[b], aw_sub_joint[b], dataset.idx2char)

            print('----- wav: %s -----' % batch['input_names'][b])
            print('Ref         : %s' % str_ref.replace('_', ' '))
            print('Hyp (main)  : %s' % str_hyp.replace('_', ' '))
            print('Hyp (sub)   : %s' % str_hyp_sub.replace('_', ' '))
            if 'OOV' in str_hyp and args.resolving_unk:
                print('Hyp (no UNK): %s' % str_hyp_no_unk.replace('_', ' '))

            # Remove noisy labels
            str_hyp = str_hyp.replace('_>', '').replace('>', '')
            str_hyp_sub = str_hyp_sub.replace('_>', '').replace('>', '')

            # Resolving UNK
            if 'OOV' in str_hyp and args.resolving_unk:
                str_hyp_no_unk = str_hyp_no_unk.replace('>', '')
                str_hyp_no_unk = re.sub(r'[_]+', '_', str_hyp_no_unk)
                if str_hyp_no_unk[-1] == '_':
                    str_hyp_no_unk = str_hyp_no_unk[:-1]

            try:
                wer, _, _, _ = compute_wer(ref=str_ref.split('_'),
                                           hyp=str_hyp.split('_'),
                                           normalize=True)
                print('WER (main)  : %.3f %%' % (wer * 100))
                if dataset.label_type_sub == 'character_wb':
                    wer_sub, _, _, _ = compute_wer(ref=str_ref_sub.split('_'),
                                                   hyp=str_hyp_sub.split('_'),
                                                   normalize=True)
                    print('WER (sub)   : %.3f %%' % (wer_sub * 100))
                else:
                    cer, _, _, _ = compute_wer(
                        ref=list(str_ref_sub.replace('_', '')),
                        hyp=list(re.sub(r'(.*)>(.*)', r'\1',
                                        str_hyp_sub).replace('_', '')),
                        normalize=True)
                    print('CER (sub)   : %.3f %%' % (cer * 100))
                if 'OOV' in str_hyp and args.resolving_unk:
                    wer_no_unk, _, _, _ = compute_wer(
                        ref=str_ref.split('_'),
                        hyp=str_hyp_no_unk.replace('*', '').split('_'),
                        normalize=True)
                    print('WER (no UNK): %.3f %%' % (wer_no_unk * 100))
            except:
                print('--- skipped ---')

            if model.model_type == 'hierarchical_attention' and args.joint_decoding is not None:
                print('===== joint decoding =====')
                print('Hyp (main)  : %s' % str_hyp_joint.replace('_', ' '))
                print('Hyp (sub)   : %s' % str_hyp_sub_joint.replace('_', ' '))
                if 'OOV' in str_hyp_joint and args.resolving_unk:
                    print('Hyp (no UNK): %s' %
                          str_hyp_no_unk_joint.replace('_', ' '))

                # Remove noisy labels
                str_hyp_joint = str_hyp_joint.replace('>', '')
                str_hyp_sub_joint = str_hyp_sub_joint.replace('>', '')

                # Remove consecutive spaces
                str_hyp_joint = re.sub(r'[_]+', '_', str_hyp_joint)
                str_hyp_sub_joint = re.sub(r'[_]+', '_', str_hyp_sub_joint)
                if str_hyp_joint[-1] == '_':
                    str_hyp_joint = str_hyp_joint[:-1]
                if str_hyp_sub_joint[-1] == '_':
                    str_hyp_sub_joint = str_hyp_sub_joint[:-1]

                if 'OOV' in str_hyp_joint and args.resolving_unk:
                    str_hyp_no_unk_joint = str_hyp_no_unk_joint.replace(
                        '>', '')
                    str_hyp_no_unk_joint = re.sub(
                        r'[_]+', '_', str_hyp_no_unk_joint)
                    if str_hyp_no_unk_joint[-1] == '_':
                        str_hyp_no_unk_joint = str_hyp_no_unk_joint[:-1]

                try:
                    wer_joint, _, _, _ = compute_wer(
                        ref=str_ref.split('_'),
                        hyp=str_hyp_joint.split('_'),
                        normalize=True)
                    print('WER (main)  : %.3f %%' % (wer_joint * 100))
                    if 'OOV' in str_hyp_joint and args.resolving_unk:
                        wer_no_unk_joint, _, _, _ = compute_wer(
                            ref=str_ref.split('_'),
                            hyp=str_hyp_no_unk_joint.replace(
                                '*', '').split('_'),
                            normalize=True)
                        print('WER (no UNK): %.3f %%' %
                              (wer_no_unk_joint * 100))
                except:
                    print('--- skipped ---')

            print('\n')

        if is_new_epoch:
            break


if __name__ == '__main__':
    main()
