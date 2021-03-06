# -*- coding: utf-8 -*-
"""
Copyright (c) 2015, Andrew Jones (andyjones dot ed at gmail dot com)
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
    * Redistributions of source code must retain the above copyright
      notice, this list of conditions and the following disclaimer.
    * Redistributions in binary form must reproduce the above copyright
      notice, this list of conditions and the following disclaimer in the
      documentation and/or other materials provided with the distribution.
    * Neither the name of the <organization> nor the
      names of its contributors may be used to endorse or promote products
      derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL <COPYRIGHT HOLDER> BE LIABLE FOR ANY
DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

"""
This module uses a the ground-truth planes of the simulated images to produce a LMDB file that can be used to train a
Caffe neural network. The main function of interest is ``make_training_files``.
"""
import scipy as sp

from simulated_tools import get_simulated_im, WINDOW_WIDTH
from caffe_tools import fill_database

"""IDs of file to be used to build the training set"""
TRAINING_IDS = ['exp_low ({0})'.format(i) for i in range(1, 25)]

"""The mapping from types of pixels to classifier labels"""
LABEL_ENUM = {'inside': 1, 
              'outside': 0, 
              'inside_damaged': 1, 
              'outside_damaged': 0, 
              'block_border': 0, 
              'between': 0}

def make_damaged_spot_mask(truth):
    """Returns a mask indicating which pixels lie inside damaged spots"""
    damaged_pixel = (0.75 < truth) & (truth < 1)
    damaged_area = sp.ndimage.binary_closing(damaged_pixel, structure=sp.ones((3, 3)))
    damaged_spot = damaged_area & (0.75 < truth)
    
    return damaged_spot
    
def make_outside_near_damaged_spot_mask(truth):
    """Returns a mask indicating which pixels lie just outside damaged spots"""
    damaged_spot = make_damaged_spot_mask(truth)
    near_damaged_spot = sp.ndimage.binary_dilation(damaged_spot, structure=sp.ones((3,3)), iterations=5)
    outside_near_damaged_spot = near_damaged_spot & (truth < 0.25)
    
    return outside_near_damaged_spot

def make_block_border_mask(truth):
    """Returns a mask indicating which pixels lie just outside a block of spots"""
    very_near_block = sp.ndimage.binary_dilation(0.75 < truth , structure=sp.ones((3,3)), iterations=3)
    near_block = sp.ndimage.binary_dilation(0.75 < truth , structure=sp.ones((3,3)), iterations=15)
    block_border = near_block & ~very_near_block
    
    return block_border
    
def make_between_spot_mask(truth):
    """Returns a mask indicating which pixels lie between two spots"""
    near_spot = sp.ndimage.binary_dilation(0.75 < truth, structure=sp.ones((3, 3)), iterations=4)
    outside_near_spot = near_spot & (truth < 0.25)
    
    return outside_near_spot

def get_centers_single_image(truth, im_no, border=20):
    """Returns a dict of arrays, one for each pixel type. The arrays are compatible with caffe_tools.fill_database.

    The last row of each array is equal to ``im_num``, indicating which image those centers were created from."""
    indices = sp.indices(truth.shape)
    im_nos = im_no*sp.ones((1, truth.shape[0], truth.shape[1]), dtype=int)
    indices = sp.concatenate((indices, im_nos))
    
    away_from_border = sp.zeros(truth.shape, dtype=bool)
    away_from_border[border:-border, border:-border] = True
    
    results = {
    'inside': indices[:, (0.75 < truth) & away_from_border]
    ,'outside': indices[:, (truth < 0.25) & away_from_border]
    ,'inside_damaged' : indices[:, make_damaged_spot_mask(truth) & away_from_border]
    ,'outside_damaged': indices[:, make_outside_near_damaged_spot_mask(truth) & away_from_border]
    ,'block_border': indices[:, make_block_border_mask(truth) & away_from_border]
    ,'between': indices[:, make_between_spot_mask(truth) & away_from_border]
    }
    
    return results
    
def get_centers(truths, border=20):
    """Uses the truths to create a dict of arrays indexed by pixel type. The arrays are compatible with 
    ``caffe_tools.fill_database``."""
    centers = []
    for i, truth in enumerate(truths):
        centers.append(get_centers_single_image(truth, i, border=border))
    
    result = {}
    for name in centers[0]:
        result[name] = sp.concatenate([cs[name] for cs in centers], 1)
        
    return result
    
def make_labelled_sets(centers, test_split=0.1):
    """Uses a dict of arrays like those created by ``get_centers`` to build test and training sets for training 
    a Caffe model to distinguish different types of pixel. The arrays returned are centers and labels compatible with 
    ``caffe_tools.fill_database``"""
    counts = {'inside': 2e5, 'outside': 1e5, 'inside_damaged': 2e5, 'outside_damaged': 1e5, 'block_border': 1e5, 'between': 1e5}
    choices = {name: sp.random.choice(sp.arange(centers[name].shape[1]), counts[name]) for name in centers}
    center_sets = {name: centers[name][:, choices[name]] for name in centers}
    label_sets = {name: sp.repeat(LABEL_ENUM[name], counts[name]) for name in centers}
    
    center_set = sp.concatenate([center_sets[name] for name in centers], 1)
    label_set = sp.concatenate([label_sets[name] for name in centers])

    order = sp.random.permutation(sp.arange(center_set.shape[1]))
    ordered_centers = center_set[:, order]
    ordered_labels = label_set[order]
    
    n_training = int((1-test_split)*center_set.shape[1])
    training_centers = ordered_centers[:, :n_training]
    training_labels = ordered_labels[:n_training]
    test_centers = ordered_centers[:, n_training:]
    test_labels = ordered_labels[n_training:]
    
    return training_centers, training_labels, test_centers, test_labels
    
def create_caffe_input_file(file_ids, width):    
    """Creates LMDB databases containing training and test sets derived from the ground truths of the simulated data. 
    ``width`` is the size of the windows to use."""  
    im_padding = ((width/2, width/2), (width/2, width/2), (0, 0))
    ims = [get_simulated_im(file_id)[0] for file_id in file_ids]
    ims = [(im - im.mean())/im.std() for im in ims]
    ims = [sp.pad(im, im_padding, mode='reflect') for im in ims]
    
    truth_padding =  ((width/2, width/2), (width/2, width/2))
    truths = [get_simulated_im(file_id)[1] for file_id in file_ids]
    truths = [sp.pad(truth, truth_padding, mode='reflect') for truth in truths]
    
    centers = get_centers(truths, width/2)
    training_centers, training_labels, test_centers, test_labels = make_labelled_sets(centers)

    fill_database('temporary/train_simulated.db', ims, training_centers, training_labels, width)
    fill_database('temporary/test_simulated.db', ims, test_centers, test_labels, width)
    
def make_training_files():
    """Uses the ground truths of the simulated data to create LMDB databases containing training and test
    sets for a Caffe neural network.
    
    The databases can be found in the ``temporary`` directory."""     
    create_caffe_input_file(TRAINING_IDS, WINDOW_WIDTH)