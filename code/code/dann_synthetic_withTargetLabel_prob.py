import random
import time
import warnings
import sys
import argparse
import copy

import torch
import torch.nn as nn
import torch.nn.parallel
import torch.backends.cudnn as cudnn
from torch.optim import SGD
import torch.utils.data
from torch.utils.data import DataLoader
import torch.utils.data.distributed
import torchvision.transforms as transforms
import torch.nn.functional as F
from collections import Counter

import pandas as pd

sys.path.append('.')
from tools.utils import AverageMeter, ProgressMeter, accuracy, ForeverDataIterator
from tools.transforms import ResizeImage
from tools.lr_scheduler import StepwiseLR
from data_processing import prepare_datasets,prepare_datasets_returnSourceVal,prepare_datasets_stratify_returnSourceVal
from feedforward import BackboneClassifierNN_M4, BackboneClassifierNN,BottleneckNN, BackboneNN
from dataset import Dataset
from dalib.modules.domain_discriminator import DomainDiscriminator

import numpy
import csv
import sklearn
from sklearn import metrics

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")


fold_loc = '/Users/yeye/Documents/CMU_course_project/Transfer-Learning/syntheticData_Exp_11082021_code_result/'
source_train_fold_loc = fold_loc + 'data/synthetic_data_v2/source_train/'
target_train_fold_loc = fold_loc + 'data/synthetic_data_v2/target_train/'
target_test_fold_loc = fold_loc + 'data/synthetic_data_v2/target_test/'
results_fold_loc = fold_loc + 'results/accuracy/'
learned_model_fold_loc = fold_loc + 'results/learned_model/dann_withTargetLabel/'
prob_fold_loc =fold_loc + 'results/auc/'


def main(args: argparse.Namespace):
    if args.seed is not None:
        random.seed(args.seed)
        torch.manual_seed(args.seed)
        cudnn.deterministic = True
        warnings.warn('You have chosen to seed training. '
                      'This will turn on the CUDNN deterministic setting, '
                      'which can slow down your training considerably! '
                      'You may see unexpected behavior when restarting '
                      'from checkpoints.')
    cudnn.benchmark = True
    # data loading code
    # create source model only by using source train and source validate data
    # input -> FC -> relu -> FC -> relu -> output num_classes
    # no softmax because that's handled by cross entropy loss already
    label_key = "diagnosis"
    validation_split = 0.125

    source_train_path = source_train_fold_loc + args.source_train_path + ".csv"
    target_train_path = target_train_fold_loc + args.target_train_path + ".csv"
    target_test_path = target_test_fold_loc + "findings_final_0814_seed-1494714102_size10000.csv"
    learned_tl_model_path = learned_model_fold_loc + args.source_train_path + "-" + args.target_train_path + "-" + str(
        args.seed) + "-dannWithTargetLabel-classifier.pth"

    csvFilename = prob_fold_loc + "dann_withTargetLabel_model_prob_" + args.source_train_path + "_" + str(args.seed) + ".csv"
    source_train_dataset, source_val_dataset, target_test_dataset, _ = prepare_datasets_returnSourceVal(
        source_train_path,
        target_train_path,
        target_test_path,
        label_key,
        validation_split)


    test_loader = DataLoader(target_test_dataset, batch_size=args.batch_size, shuffle=False,
                             num_workers=args.workers)

    # create model
    backbone_in_dim = source_train_dataset.features.shape[1]
    num_classes = 4
    backbone = BackboneNN(backbone_in_dim)
    classifier = BottleneckNN(backbone, num_classes).to(device)
    domain_discri = DomainDiscriminator(in_feature=classifier.features_dim, hidden_size=1024).to(device)
    print(classifier)

    # load source model
    print("load best model")
    classifier.load_state_dict(torch.load(learned_tl_model_path))
    print(classifier)
    # Print model's state_dict
    print("Model's state_dict:")
    for param_tensor in classifier.state_dict():
        print(param_tensor, "\t", classifier.state_dict()[param_tensor].size())
    # evaluate on test set
    test_acc1,total_y_true,total_y_pred1,total_y_pred2,total_y_pred3,total_y_pred4,total_y_diagnosis,total_y_correct = validate(test_loader, classifier, args)
    print("test_acc1 = {:3.1f}".format(test_acc1))
    avg_auc,auc_I,auc_M,auc_P,auc_R = printListToFile(csvFilename,total_y_true,total_y_pred1,total_y_pred2,total_y_pred3,total_y_pred4,total_y_diagnosis,total_y_correct)
    return test_acc1, avg_auc,auc_I,auc_M,auc_P,auc_R







def printListToFile(fileName,total_y_true,total_y_pred1,total_y_pred2,total_y_pred3,total_y_pred4,total_y_diagnosis,total_y_correct):
    a = numpy.asarray(total_y_true).astype(int)
    b1 = numpy.asarray(total_y_pred1)
    b2 = numpy.asarray(total_y_pred2)
    b3 = numpy.asarray(total_y_pred3)
    b4 = numpy.asarray(total_y_pred4)
    c = numpy.asarray(total_y_diagnosis).astype(int)
    d = numpy.asarray(total_y_correct).astype(int)
    df = pd.DataFrame({"y_true": a, "p0": b1, "p1": b2, "p2": b3, "p3": b4, "prediction": c, "correct": d})
    df.to_csv(fileName, index=False)

    # calculate auc
    # I:0; M:1; P:2; R:3
    df['I_category'] = 'F'
    df.loc[df['y_true'] == 0, "I_category"] = "T"
    df['M_category'] = 'F'
    df.loc[df['y_true'] == 1, "M_category"] = "T"
    df['P_category'] = 'F'
    df.loc[df['y_true'] == 2, "P_category"] = "T"
    df['R_category'] = 'F'
    df.loc[df['y_true'] == 3, "R_category"] = "T"
    fpr, tpr, thresholds = metrics.roc_curve(df['I_category'], df['p0'], pos_label='T')
    auc_I = metrics.auc(fpr, tpr)
    fpr, tpr, thresholds = metrics.roc_curve(df['M_category'], df['p1'], pos_label='T')
    auc_M = metrics.auc(fpr, tpr)
    fpr, tpr, thresholds = metrics.roc_curve(df['P_category'], df['p2'], pos_label='T')
    auc_P = metrics.auc(fpr, tpr)
    fpr, tpr, thresholds = metrics.roc_curve(df['R_category'], df['p3'], pos_label='T')
    auc_R = metrics.auc(fpr, tpr)
    avg_auc = (auc_I + auc_M + auc_P + auc_R) / 4
    return avg_auc,auc_I,auc_M,auc_P,auc_R



#def validate(val_loader: DataLoader, model: nn.Module, args: argparse.Namespace):
def validate(val_loader: DataLoader, model: BottleneckNN, args: argparse.Namespace) -> float:
    total_y_pred1 = numpy.array([[]])
    total_y_pred2 = numpy.array([[]])
    total_y_pred3 = numpy.array([[]])
    total_y_pred4 = numpy.array([[]])
    total_y_diagnosis = numpy.array([[]])
    total_y_correct = numpy.array([[]])
    total_y_true = numpy.array([])

    batch_time = AverageMeter('Time', ':6.3f')
    losses = AverageMeter('Loss', ':.4e')
    top1 = AverageMeter('Acc@1', ':6.2f')
    progress = ProgressMeter(
        len(val_loader),
        [batch_time, losses, top1],
        prefix='Test: ')
    
    # switch to evaluate mode
    model.eval()

    with torch.no_grad():
        end = time.time()
        for i, (features, target) in enumerate(val_loader):
            features = features.to(device)
            target = target.to(device)

            # compute output
            output, _ = model(features)
            loss = F.cross_entropy(output, target)

            # measure accuracy and record loss
            acc1 = accuracy(output, target)
            losses.update(loss.item(), features.size(0))
            top1.update(acc1[0].item(), features.size(0))

            # measure elapsed time
            batch_time.update(time.time() - end)
            end = time.time()

            if i % args.print_freq == 0:
                progress.display(i)

            probAll = torch.nn.functional.softmax(output, dim=1).cpu().numpy()
            total_y_pred1 = numpy.append(total_y_pred1, probAll[:, 0])
            total_y_pred2 = numpy.append(total_y_pred2, probAll[:, 1])
            total_y_pred3 = numpy.append(total_y_pred3, probAll[:, 2])
            total_y_pred4 = numpy.append(total_y_pred4, probAll[:, 3])
            total_y_true = numpy.append(total_y_true, target.cpu().numpy())
            _, diagnosis = output.topk(1, 1, True, True)
            diagnosis = diagnosis.t()
            total_y_diagnosis = numpy.append(total_y_diagnosis, diagnosis.cpu().numpy())
            correct = diagnosis.eq(target.view(1, -1).expand_as(diagnosis))
            total_y_correct = numpy.append(total_y_correct, correct.cpu().numpy())
        print(' * Acc@1 {top1.avg:.3f}'
              .format(top1=top1))
    return top1.avg,total_y_true,total_y_pred1,total_y_pred2,total_y_pred3,total_y_pred4,total_y_diagnosis,total_y_correct

def generateIntegarArray(size):
    list=[]
    n = 0
    while n<size:
        temp = random.randint(0, 100000)
        if temp not in list:
            list.append(temp)
            n = n+1
    return list




if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='PyTorch Domain Adaptation')
    parser.add_argument('-j', '--workers', default=0, type=int, metavar='N',
                        help='number of data loading workers (default: 4)')
    parser.add_argument('--epochs', default=10, type=int, metavar='N',
                        help='number of total epochs to run')
    parser.add_argument('-b', '--batch-size', default=32, type=int,
                        metavar='N',
                        help='mini-batch size (default: 32)')
    parser.add_argument('--lr', '--learning-rate', default=0.01, type=float,
                        metavar='LR', help='initial learning rate', dest='lr')
    parser.add_argument('--momentum', default=0.9, type=float, metavar='M',
                        help='momentum')
    parser.add_argument('--wd', '--weight-decay',default=1e-3, type=float,
                        metavar='W', help='weight decay (default: 1e-3)',
                        dest='weight_decay')
    parser.add_argument('-p', '--print-freq', default=100, type=int,
                        metavar='N', help='print frequency (default: 100)')
    parser.add_argument('--seed', default=None, type=int,
                        help='seed for initializing training. ')
    parser.add_argument('--trade-off', default=1., type=float,
                        help='the trade-off hyper-parameter for transfer loss')
    parser.add_argument('-i', '--iters-per-epoch', default=313, type=int,
                        help='Number of iterations per epoch')

    args = parser.parse_args()
    print(args)

    source_train_paths = ['findings_final_0814_seed1591536269_size10000',
                      'findings_final_0814-portion1ita06round14_seed2016863826_size10000',
                      'findings_final_0814-portion1ita13round20_seed1708886178_size10000',
                      'findings_final_0814-portion1ita16round14_seed1948253030_size10000',
                      'findings_final_0814-portion1ita21round14_seed1879396416_size10000',
                      'findings_final_0814-portion1ita27round9_seed1940262766_size10000',
                      'findings_final_0814-portion1ita27round9_seed273823007_size10000',
                      'findings_final_0814-portion1ita28round3_seed-279490714_size10000',
                      'findings_final_0814-portion1ita29round18_seed-1653352491_size10000',
                      'findings_final_0814-portion1ita21round14_seed-358819036_size10000',
                      'findings_final_0814-portion1ita21round14_seed174506763_size10000',
                      'findings_final_0814-portion1ita21round14_seed-1580326299_size10000',
                      'findings_final_0814-portion1ita21round14_seed1514764506_size10000']



    target_train_paths = ['findings_final_0814_seed2132231585_size10000',
                          'findings_final_0814_seed-190708218_size5000',
                          'findings_final_0814_seed-1872107095_size4000',
                          'findings_final_0814_seed678668699_size3000',
                          'findings_final_0814_seed1033059257_size2000',
                          'findings_final_0814_seed238506806_size1000',
                          'findings_final_0814_seed-972126700_size500',
                          'findings_final_0814_seed-1133351443_size400',
                          'findings_final_0814_seed-1227021050_size300',
                          'findings_final_0814_seed756906437_size200',
                          'findings_final_0814_seed-1331694080_size100',
                          'findings_final_0814_seed-53154026_size50']


    d_kl_dict = {}
    d_kl_dict['findings_final_0814'] = 0
    d_kl_dict['findings_final_0814-portion1ita06round14'] = 1
    d_kl_dict['findings_final_0814-portion1ita13round20'] = 5
    d_kl_dict['findings_final_0814-portion1ita16round14'] = 10
    d_kl_dict['findings_final_0814-portion1ita21round14'] = 15
    d_kl_dict['findings_final_0814-portion1ita27round9'] = 20
    d_kl_dict['findings_final_0814-portion1ita28round3'] = 25
    d_kl_dict['findings_final_0814-portion1ita29round18'] = 30


    seed_paths = [14942, 43277, 79280, 8463, 12650]

    with open(results_fold_loc + "/auc_dann_withTargetVal_log11222021.txt", "w") as f:
        f.write(f"d_kl,source_train_path,target_train_path,seed_index,seed,test_acc,avg_auc,auc_I,auc_M,auc_P,auc_R\n")
        for i in range(len(target_train_paths)):
            args.target_train_path = target_train_paths[i]
            for j in range(len(source_train_paths)):
                args.source_train_path = source_train_paths[j]
                d_kl = -1
                for key in d_kl_dict.keys():
                    if key in args.source_train_path:
                        d_kl = d_kl_dict[key]
                for seed_index in range(len(seed_paths)):
                    args.seed = seed_paths[seed_index]
                    test_acc1, avg_auc, auc_I, auc_M, auc_P, auc_R = main(args)
                    f.write(
                        f"{d_kl},{args.source_train_path},{args.target_train_path},{seed_index},{args.seed},{test_acc1},{avg_auc},{auc_I},{auc_M},{auc_P},{auc_R}\n")