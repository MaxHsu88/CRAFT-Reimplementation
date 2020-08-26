import os
import sys
import torch
import torch.utils.data as data
import cv2
import numpy as np
import scipy.io as scio
import argparse
import time
import torch.nn as nn
import torch.nn.functional as F
import torch.backends.cudnn as cudnn
import torch.optim as optim
import random
import h5py
import re
# import water

from data_loader import ICDAR2015, Synth80k, ICDAR2013, PRL5fold

from test import test

from math import exp

###import file#######
# from augmentation import random_rot, crop_img_bboxes
# from gaussianmap import gaussion_transform, four_point_transform
# from generateheatmap import add_character, generate_target, add_affinity, generate_affinity, sort_box, real_affinity, generate_affinity_box
from mseloss import Maploss

from collections import OrderedDict
from eval.script import getresult

from PIL import Image
from torchvision.transforms import transforms
from craft import CRAFT
from torch.autograd import Variable
from multiprocessing import Pool

#3.2768e-5
random.seed(42)

parser = argparse.ArgumentParser(description='CRAFT reimplementation')


parser.add_argument('--resume', default=None, type=str,
                    help='Checkpoint state_dict file to resume training from')
parser.add_argument('--batch_size', default=12, type = int,
                    help='batch size of training')
parser.add_argument('--cuda', default=True, type=bool,
                    help='Use CUDA to train model')
parser.add_argument('--lr', '--learning-rate', default=3.2768e-5, type=float,
                    help='initial learning rate')
parser.add_argument('--momentum', default=0.9, type=float,
                    help='Momentum value for optim')
parser.add_argument('--weight_decay', default=5e-4, type=float,
                    help='Weight decay for SGD')
parser.add_argument('--gamma', default=0.1, type=float,
                    help='Gamma update for SGD')
parser.add_argument('--num_workers', default=8, type=int,
                    help='Number of workers used in dataloading')
# parser.add_argument('--fold_number', default=3, type=int,
#                     help='determine which fold to be trained on')
parser.add_argument('--start_epoch', default=2, type=int,
                    help='the number of epoches you start')


args = parser.parse_args()

os.environ["CUDA_VISIBLE_DEVICES"] = "0,1,2,3"

def copyStateDict(state_dict):
    if list(state_dict.keys())[0].startswith("module"):
        start_idx = 1
    else:
        start_idx = 0
    new_state_dict = OrderedDict()
    for k, v in state_dict.items():
        name = ".".join(k.split(".")[start_idx:])
        new_state_dict[name] = v
    return new_state_dict


def adjust_learning_rate(optimizer, gamma, step):
    """Sets the learning rate to the initial LR decayed by 10 at every
        specified step
    # Adapted from PyTorch Imagenet example:
    # https://github.com/pytorch/examples/blob/master/imagenet/main.py
    """
    lr = args.lr * (0.8 ** step)
    print(lr)
    for param_group in optimizer.param_groups:
        param_group['lr'] = lr


if __name__ == '__main__':

    # device = torch.device("cuda:2,3")

    net = CRAFT()
    net.load_state_dict(copyStateDict(torch.load('./epoch_weights/SciTSR/SciTSR_epoch_1_batch_950.pth')))
    net = net.cuda()

    if args.cuda:
        net = torch.nn.DataParallel(net,device_ids=[0,1,2,3]).cuda()

    net.train()
    cudnn.benchmark = False

    dataloader = Synth80k('./data/CRAFT-pytorch/SynthText/', target_size = 768)
    train_loader = torch.utils.data.DataLoader(
        dataloader,
        batch_size=2,
        shuffle=True,
        num_workers=0,
        drop_last=True,
        pin_memory=True)
    batch_syn = iter(train_loader)

    # realdata = PRL5fold(net, './data/PRL_5fold/fold%d/train/' % (args.fold_number), target_size = 768, viz = False)
    realdata = PRL5fold(net, './data/SciTSR/train/', target_size = 768, viz = False)
    real_data_loader = torch.utils.data.DataLoader(
        realdata,
        batch_size=10,
        shuffle=True,
        num_workers=0,
        drop_last=True,
        pin_memory=True)
    
    net.train()

    optimizer = optim.Adam(net.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    criterion = Maploss()


    step_index = 0

    loss_time = 0
    loss_value = 0
    compare_loss = 1
    for epoch in range(args.start_epoch, 1000):
        train_time_st = time.time()
        loss_value = 0
        if epoch % 27 == 0 and epoch != 0:
            step_index += 1
            adjust_learning_rate(optimizer, args.gamma, step_index)

        st = time.time()
        for index, (real_images, real_gh_label, real_gah_label, real_mask, _) in enumerate(real_data_loader):

            net.train()

            syn_images, syn_gh_label, syn_gah_label, syn_mask, __ = next(batch_syn)
            images = torch.cat((syn_images,real_images), 0)
            gh_label = torch.cat((syn_gh_label, real_gh_label), 0)
            gah_label = torch.cat((syn_gah_label, real_gah_label), 0)
            mask = torch.cat((syn_mask, real_mask), 0)
            #affinity_mask = torch.cat((syn_mask, real_affinity_mask), 0)

            images = Variable(images.type(torch.FloatTensor)).cuda()
            gh_label = gh_label.type(torch.FloatTensor)
            gah_label = gah_label.type(torch.FloatTensor)
            gh_label = Variable(gh_label).cuda()
            gah_label = Variable(gah_label).cuda()
            mask = mask.type(torch.FloatTensor)
            mask = Variable(mask).cuda()
            # affinity_mask = affinity_mask.type(torch.FloatTensor)
            # affinity_mask = Variable(affinity_mask).cuda()

            out, _ = net(images)

            optimizer.zero_grad()

            out1 = out[:, :, :, 0].cuda()
            out2 = out[:, :, :, 1].cuda()
            loss = criterion(gh_label, gah_label, out1, out2, mask)

            loss.backward()
            optimizer.step()
            loss_value += loss.item()
            if index % 2 == 0 and index > 0:
                et = time.time()
                print('epoch {}:({}/{}) batch || training time for 2 batch {} || training loss {} ||'.format(epoch, index, len(real_data_loader), et-st, loss_value/2))
                loss_time = 0
                loss_value = 0
                st = time.time()
            # if loss < compare_loss:
            #     print('save the lower loss iter, loss:',loss)
            #     compare_loss = loss
            #     torch.save(net.module.state_dict(),
            #                './epoch_weights/PRL/PRL' + '_epoch_' + repr(epoch) + '_batch_' + repr(index) + '.pth')

            if index % 50 == 0:
                print('saving the weights... (epoch %d, iter %d)' % (epoch, index))
                torch.save(net.module.state_dict(),
                           './epoch_weights/SciTSR/SciTSR' + '_epoch_' + repr(epoch) + '_batch_' + repr(index) + '.pth')

            net.eval()

        print('Saving state, iter:', epoch)
        torch.save(net.module.state_dict(),
                   './epoch_weights/SciTSR/SciTSR' + '_' + repr(epoch) + '.pth')
        
        # 測試 model 的效能
        # test('./data/CRAFT-pytorch/epoch_weights/mlt' + '_' + repr(epoch) + '.pth')
        
        # getresult()
