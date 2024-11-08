import torch
import torch.nn as nn
from torch.autograd import Variable
from data import CreateTrgDataLoader
from PIL import Image
import json
import os.path as osp
import os
import numpy as np
from model import CreateModel
import tqdm
from utils import root_base
import argparse


result_store =root_base+'/snapshots/'


def parse_args():
    parser = argparse.ArgumentParser(description="adaptive segmentation netowork")
    parser.add_argument("--model", type=str, default='VGG',help="available options : DeepLab and VGG")
    parser.add_argument("--data-dir-target", type=str, default=root_base + '/dataset/cityscapes', help="Path to the directory containing the source dataset.")
    parser.add_argument("--data-list-target", type=str, default=root_base +'/dataset/cityscapes_list/val.txt', help="Path to the file listing the images in the source dataset.")
    parser.add_argument("--data-label-folder-target", type=str, default=None, help="Path to the soft assignments in the target dataset.") 
    parser.add_argument("--num-classes", type=int, default=4, help="Number of classes for cityscapes.")
    parser.add_argument("--init-weights", type=str, default=None, help="initial model.")
    parser.add_argument("--set", type=str, default='val', help="choose adaptation set.")  
    parser.add_argument("--model-name", type=str, default=None, help="Model's file name")
    parser.add_argument("--restore-from", type=str, default=None, help="Restore model's folder.") 
    parser.add_argument("--save", type=str, default=root_base+'/dataset/cityscapes/results', help="Path to save result.")    
    parser.add_argument('--gt_dir', type=str, default = root_base +'/dataset/cityscapes/labels', help='directory which stores CityScapes val gt images')
    parser.add_argument('--devkit_dir', default=root_base+'/dataset/cityscapes_list', help='base directory of cityscapes')         
    return parser.parse_args()


palette = [0, 0, 0, 255, 0, 0, 0,255,0,255,255,0]
zero_pad = 256 * 3 - len(palette)
for i in range(zero_pad):
    palette.append(0)

def self_entropy(pred, epsilon=1e-12):
    pred = pred[0]
    p = pred * np.log(pred+ epsilon)
    map_ = -np.sum(p, -1)
    
    return map_
    
def roll_axis(inp):
    inp = np.rollaxis(inp,axis =-1)
    inp = np.rollaxis(inp,axis =-1)
    inp = inp.reshape(1,inp.shape[0],inp.shape[1],inp.shape[2])
    
    return inp
def colorize_mask(mask):
    # mask: numpy array of the mask
    new_mask = Image.fromarray(mask.astype(np.uint8)).convert('P')
    new_mask.putpalette(palette)

    return new_mask
def fast_hist(a, b, n):
    k = (a >= 0) & (a < n)
    return np.bincount(n * a[k].astype(int) + b[k], minlength=n ** 2).reshape(n, n)


def per_class_iu(hist):
    return np.diag(hist) / (hist.sum(1) + hist.sum(0) - np.diag(hist))


def label_mapping(input, mapping):
    output = np.copy(input)
    for ind in range(len(mapping)):
        output[input == mapping[ind][0]] = mapping[ind][1]
    return np.array(output, dtype=np.int64)

def compute_mIoU(gt_dir, pred_dir, devkit_dir='', restore_from=''):
    with open(osp.join(devkit_dir, 'info.json'), 'r') as fp:
        info = json.load(fp)
    num_classes = np.int(info['classes'])
    print('Num classes', num_classes)
    name_classes = np.array(info['label'], dtype=np.str)
    mapping = np.array(info['label2train'], dtype=np.int)
    hist = np.zeros((num_classes, num_classes))

    image_path_list = osp.join(devkit_dir, 'val.txt')
    label_path_list = osp.join(devkit_dir, 'val.txt')
    gt_imgs = open(label_path_list, 'r').read().splitlines()
    gt_imgs = [osp.join(gt_dir, x) for x in gt_imgs]
    pred_imgs = open(image_path_list, 'r').read().splitlines()
    pred_imgs = [osp.join(pred_dir, x.split('/')[-1]) for x in pred_imgs]

    for ind in range(len(gt_imgs)):
        pred = np.array(Image.open(pred_imgs[ind]))
        label = np.array(Image.open(gt_imgs[ind]))
        label = label_mapping(label, mapping)
        if len(label.flatten()) != len(pred.flatten()):
            print('Skipping: len(gt) = {:d}, len(pred) = {:d}, {:s}, {:s}'.format(len(label.flatten()), len(pred.flatten()), gt_imgs[ind], pred_imgs[ind]))
            continue
        hist += fast_hist(label.flatten(), pred.flatten(), num_classes)
        #if ind > 0 and ind % 10 == 0:
            #with open(result_store+'_mIoU.txt', 'a') as f:
                #f.write('{:d} / {:d}: {:0.2f}\n'.format(ind, len(gt_imgs), 100*np.mean(per_class_iu(hist))))
            #print('{:d} / {:d}: {:0.2f}'.format(ind, len(gt_imgs), 100*np.mean(per_class_iu(hist))))
    hist2 = np.zeros((4, 4))
    for i in range(4):
        hist2[i] = hist[i] / np.sum(hist[i])
    
    mIoUs = per_class_iu(hist)
    for ind_class in range(num_classes):
        with open(result_store+'mIoU.txt', 'a') as f:
            f.write('===>' + name_classes[ind_class] + ':\t' + str(round(mIoUs[ind_class] * 100, 2)) + '\n')
        print('===>' + name_classes[ind_class] + ':\t' + str(round(mIoUs[ind_class] * 100, 2)))
    with open(result_store+'mIoU.txt', 'a') as f:
        f.write('===> mIoU: ' + str(round(np.nanmean(mIoUs) * 100, 2)) + '\n')
    
    return round(np.nanmean(mIoUs) * 100, 2)


def main():
    testing_entropy = 0
   
    args = parse_args()    
    
    if not os.path.exists(args.save):
        os.makedirs(args.save)
        
    args.init_weights = root_base+'/snapshots/' + args.model_name
    
    model = CreateModel(args)
    
    
    model.eval()
    model.cuda()    
    targetloader = CreateTrgDataLoader(args)
    
    for index, batch in tqdm.tqdm(enumerate(targetloader)):
        
        image, _, name,_ = batch
        output = model(Variable(image).cuda())
        output = nn.functional.softmax(output, dim=1)
        testing_entropy += self_entropy(roll_axis(output.cpu().data[0].numpy()))
        output = nn.functional.upsample(output, (256, 256), mode='bilinear', align_corners=True).cpu().data[0].numpy()
        #output = np.multiply(output,priors)
        output = output.transpose(1,2,0)
        output_nomask = np.asarray(np.argmax(output, axis=2), dtype=np.uint8)
        output_col = colorize_mask(output_nomask)
        output_nomask = Image.fromarray(output_nomask)    
        name = name[0].split('/')[-1]
        output_nomask.save('%s/%s' % (args.save, name))
        output_col.save('%s/%s_color.png' % (args.save, name.split('.')[0])) 
        
    mIou_ = compute_mIoU(args.gt_dir, args.save, args.devkit_dir, '')    

    # return testing_entropy, mIou_
    
if __name__ == "__main__":
    main()
