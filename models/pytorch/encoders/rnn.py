#! /usr/bin/env python
# -*- coding: utf-8 -*-

"""RNN encoders."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import math
import torch
import torch.nn as nn
from torch.autograd import Variable
from torch.nn.utils.rnn import pack_padded_sequence, pad_packed_sequence

from utils.io.variable import var2np


class RNNEncoder(nn.Module):
    """RNN encoder.
    Args:
        input_size (int): the dimension of input features
        rnn_type (string): lstm or gru or rnn
        bidirectional (bool): if True, use the bidirectional encoder
        num_units (int): the number of units in each layer
        num_proj (int): the number of nodes in the projection layer
        num_layers (int): the number of layers
        dropout (float): the probability to drop nodes
        parameter_init (float): the range of uniform distribution to
            initialize weight parameters (>= 0)
        use_cuda (bool, optional): if True, use GPUs
        batch_first (bool, optional): if True, batch-major computation will be
            performed
        merge_bidirectional (bool, optional): if True, sum bidirectional outputs
        num_stack (int, optional): the number of frames to stack
        splice (int, optional): frames to splice. Default is 1 frame.
        channels (list, optional):
        kernel_sizes (list, optional):
        strides (list, optional):
    """

    def __init__(self,
                 input_size,
                 rnn_type,
                 bidirectional,
                 num_units,
                 num_proj,
                 num_layers,
                 dropout,
                 parameter_init,
                 use_cuda=False,
                 batch_first=False,
                 merge_bidirectional=False,
                 num_stack=1,
                 splice=1,
                 channels=[],
                 kernel_sizes=[],
                 strides=[]):

        super(RNNEncoder, self).__init__()

        self.rnn_type = rnn_type
        self.bidirectional = bidirectional
        self.num_directions = 2 if bidirectional else 1
        self.num_units = num_units
        self.num_proj = num_proj
        self.num_layers = num_layers
        self.use_cuda = use_cuda
        self.batch_first = batch_first
        self.merge_bidirectional = merge_bidirectional

        # Setting for CNNs before RNNs
        self.splice = splice
        self.num_stack = num_stack
        self.input_channels = 3
        self.input_freq = input_size // self.input_channels

        if len(channels) > 0 and len(channels) == len(kernel_sizes) and len(kernel_sizes) == len(strides):
            convs = []
            in_c = self.input_channels
            assert input_size % in_c == 0
            for i in range(len(channels)):
                assert kernel_sizes[i][0] % 2 == 1
                assert kernel_sizes[i][1] % 2 == 1

                convs.append(nn.Conv2d(
                    in_channels=in_c,
                    out_channels=channels[i],
                    kernel_size=tuple(kernel_sizes[i]),
                    stride=tuple(strides[i]),
                    padding=(kernel_sizes[i][0] // 2, kernel_sizes[i][1] // 2)))
                convs.append(nn.ReLU())
                convs.append(nn.Dropout(p=dropout))
                in_c = channels[i]
            self.conv = nn.Sequential(*convs)

            out_freq = self.input_freq
            out_time = splice * num_stack
            for f, t in strides:
                out_freq = math.ceil(out_freq / f)
                out_time = math.ceil(out_time / t)
            input_size = channels[-1] * out_freq * out_time
        else:
            input_size = input_size * splice * num_stack
            self.conv = None

        # NOTE: dropout is applied except the last layer
        if rnn_type == 'lstm':
            self.rnn = nn.LSTM(
                input_size,
                hidden_size=num_units,
                num_layers=num_layers,
                bias=True,
                batch_first=batch_first,
                dropout=dropout,
                bidirectional=bidirectional)
        elif rnn_type == 'gru':
            self.rnn = nn.GRU(
                input_size,
                hidden_size=num_units,
                num_layers=num_layers,
                bias=True,
                batch_first=batch_first,
                dropout=dropout,
                bidirectional=bidirectional)
        elif rnn_type == 'rnn':
            self.rnn = nn.RNN(
                input_size,
                hidden_size=num_units,
                num_layers=num_layers,
                bias=True,
                batch_first=batch_first,
                dropout=dropout,
                bidirectional=bidirectional)
        else:
            raise TypeError('rnn_type must be "lstm" or "gru" or "rnn".')

    def _init_hidden(self, batch_size, volatile):
        """Initialize hidden states.
        Args:
            batch_size (int): the size of mini-batch
            volatile (bool): if True, the history will not be saved.
                This should be used in inference model for memory efficiency.
        Returns:
            if rnn_type is 'lstm', return a tuple of tensors (h_0, c_0).
                h_0: A tensor of size
                    `[num_layers * num_directions, batch_size, num_units]`
                c_0: A tensor of size
                    `[num_layers * num_directions, batch_size, num_units]`
            otherwise return h_0.
        """
        h_0 = Variable(torch.zeros(
            self.num_layers * self.num_directions, batch_size, self.num_units))

        if volatile:
            h_0.volatile = True

        if self.use_cuda:
            h_0 = h_0.cuda()

        if self.rnn_type == 'lstm':
            c_0 = Variable(torch.zeros(
                self.num_layers * self.num_directions, batch_size, self.num_units))

            if volatile:
                c_0.volatile = True

            if self.use_cuda:
                c_0 = c_0.cuda()

            return (h_0, c_0)
        else:
            return h_0

    def forward(self, inputs, inputs_seq_len, volatile=False,
                mask_sequence=True):
        """Forward computation.
        Args:
            inputs (FloatTensor): A tensor of size `[B, T, input_size]`
            inputs_seq_len (IntTensor or LongTensor): A tensor of size `[B]`
            volatile (bool, optional): if True, the history will not be saved.
                This should be used in inference model for memory efficiency.
            mask_sequence (bool, optional): if True, mask by sequence
                lenghts of inputs
        Returns:
            outputs:
                if batch_first is True, a tensor of size
                    `[B, T, num_units (* num_directions)]`
                else
                    `[T, B, num_units (* num_directions)]`
            final_state_fw: A tensor of size `[1, B, num_units]`
            perm_indices ():
        """
        batch_size, max_time, input_size = inputs.size()

        # Initialize hidden states (and memory cells) per mini-batch
        h_0 = self._init_hidden(batch_size=batch_size, volatile=volatile)

        if mask_sequence:
            # Sort inputs by lengths in descending order
            inputs_seq_len, perm_indices = inputs_seq_len.sort(
                dim=0, descending=True)
            inputs = inputs[perm_indices]
        else:
            perm_indices = None

        # Path through CNN layers before RNN layers
        if self.conv is not None:
            # for debug
            # print('input_size: %d' % input_size)
            # print('input_freq: %d' % self.input_freq)
            # print('input_channels %d' % self.input_channels)
            # print('splice: %d' % self.splice)
            # print('num_stack: %d' % self.num_stack)

            assert input_size == self.input_freq * \
                self.input_channels * self.splice * self.num_stack

            # Reshape to 4D tensor
            inputs = inputs.view(
                batch_size * max_time, self.input_channels,
                self.input_freq, self.splice * self.num_stack)

            # print(inputs.size())
            inputs = self.conv(inputs)
            # print(inputs.size())

            output_channels, freq, time = inputs.size()[1:]

            # Collapse feature dimension
            inputs = inputs.view(batch_size, -1,
                                 output_channels * freq * time)
            # print(inputs.size())

        if not self.batch_first:
            # Reshape inputs to the time-major
            inputs = inputs.transpose(0, 1).contiguous()

        if not isinstance(inputs_seq_len, list):
            inputs_seq_len = var2np(inputs_seq_len).tolist()

        if mask_sequence:
            # Pack encoder inputs
            inputs = pack_padded_sequence(
                inputs, inputs_seq_len, batch_first=self.batch_first)

        if self.rnn_type == 'lstm':
            outputs, (h_n, c_n) = self.rnn(inputs, hx=h_0)
        else:
            outputs, h_n = self.rnn(inputs, hx=h_0)

        if mask_sequence:
            # Unpack encoder outputs
            outputs, unpacked_seq_len = pad_packed_sequence(
                outputs, batch_first=self.batch_first)
            # TODO: update version for padding_value=0.0
            assert inputs_seq_len == unpacked_seq_len

        # Sum bidirectional outputs
        if self.bidirectional and self.merge_bidirectional:
            outputs = outputs[:, :, :self.num_units] + \
                outputs[:, :, self.num_units:]

        # Pick up the final state of the top layer (forward)
        if self.num_directions == 2:
            final_state_fw = h_n[-2:-1, :, :]
        else:
            final_state_fw = h_n[-1, :, :].unsqueeze(dim=0)
        # NOTE: h_n: `[num_layers * num_directions, B, num_units]`

        # TODO: add the projection layer

        return outputs, final_state_fw, perm_indices
