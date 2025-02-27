from re import sub
import torch
import math
import tqdm
# from tqdm import tqdm
import numpy as np
import json
import time
from pynndescent import NNDescent
from sklearn.neighbors import KDTree
from sklearn.metrics import pairwise_distances
from scipy import stats as stats
from sklearn.neighbors import NearestNeighbors

def _construct_fuzzy_complex(train_data, metric="euclidean"):
    # """
    # construct a vietoris-rips complex
    # """
    # number of trees in random projection forest
    n_trees = min(64, 5 + int(round((train_data.shape[0]) ** 0.5 / 20.0)))
    # max number of nearest neighbor iters to perform
    n_iters = max(5, int(round(np.log2(train_data.shape[0]))))
    # distance metric
    # # get nearest neighbors
    
    nnd = NNDescent(
        train_data,
        n_neighbors=15,
        metric=metric,
        n_trees=n_trees,
        n_iters=n_iters,
        max_candidates=60,
        verbose=True
    )
    knn_indices, knn_dists = nnd.neighbor_graph
    return knn_indices

def mixup_bi(model, image1, image2, label, target_cls, device, diff=0.1, max_iter=8, l_bound=0.8):
    '''Get BPs based on mixup method, fast
    :param model: subject model
    :param image1: images, torch.Tensor of shape (N, C, H, W)
    :param image2: images, torch.Tensor of shape (N, C, H, W)
    :param label: int, 0-9, prediction for image1
    :param target_cls: int, 0-9, prediction for image2
    :param device: the device to run code, torch cpu or torch cuda
    :param diff: the difference between top1 and top2 logits we define as boundary, float by default 0.1
    :param max_iter: binary search iteration maximum value, int by default 8
    :param verbose: whether to print verbose message, int by default 1
    '''
    def f(x):
        # New prediction
        with torch.no_grad():
            x = x.to(device, dtype=torch.float)
            pred_new = model(x)
            conf_max = torch.max(pred_new.detach().cpu(), dim=1)[0]
            conf_min = torch.min(pred_new.detach().cpu(), dim=1)[0]
            normalized = (pred_new.detach().cpu() - conf_min) / (conf_max - conf_min)  # min-max rescaling
        return pred_new, normalized

    # initialze upper and lower bound
    # set a limitation to upper bound
    upper = 1
    lower = l_bound
    successful = False

    for step in range(max_iter):
        # take middle point
        lamb = (upper + lower) / 2
        image_mix = lamb * image1 + (1 - lamb) * image2

        pred_new, normalized = f(image_mix)

        # Bisection method
        if normalized[0, label] - normalized[0, target_cls] > 0:  
            # shall decrease weight on image 1
            upper = lamb

        else:  
            # shall increase weight on image 1
            lower = lamb
        # Stop when ...
        # reach the decision boundary, and
        # abs(upper-lower) < 0.1 or step>1
        sorted, _ = torch.sort(normalized[0])
        curr_diff = sorted[-1] - sorted[-2]
        if curr_diff < diff and (upper-lower) < 0.1:
        # if torch.abs(normalized[0, label] - normalized[0, target_cls]).item() < diff and (upper-lower) < 0.1:
            successful = True
            break
    return image_mix, successful, step


def get_border_points(model, input_x, confs, predictions, device, num_adv_eg, l_bound=0.6, lambd=0.2, verbose=1):
    '''Get BPs
    :param model: subject model
    :param input_x: images, torch.Tensor of shape (N, C, H, W)
    :param confs: logits, numpy.ndarray of shape (N, class_num)
    :param predictions: class prediction, numpy.ndarray of shape (N,)
    :param num_adv_eg: number of adversarial examples to be generated, int
    :param l_bound: lower bound to conduct mix-up attack, range (0, 1)
    :param lambd: trade-off between efficiency and diversity, (0, 1)
    :return adv_examples: adversarial images, torch.Tensor of shape (N, C, H, W)
    '''

    adv_examples = torch.tensor([]).to(device)
    num_adv = 0
    a = lambd
    # count valid classes
    valid_cls = np.unique(predictions)
    valid_cls_num = len(valid_cls)
    if valid_cls_num < 2:
        raise Exception("Valid prediction classes less than 2!")

    succ_rate = np.ones(int(valid_cls_num*(valid_cls_num-1)/2))
    tot_num = np.zeros(int(valid_cls_num*(valid_cls_num-1)/2))
    curr_samples = np.zeros(int(valid_cls_num*(valid_cls_num-1)/2))

    # record index dictionary for query index pair
    idx = 0
    index_dict = dict()
    for i in range(valid_cls_num):
        for j in range(i+1, len(valid_cls)):
            index_dict[idx] = (valid_cls[i], valid_cls[j])
            idx += 1

    t0 = time.time()

    pbar = tqdm.tqdm(total=num_adv_eg, desc="Generating adversarial examples")
    while num_adv < num_adv_eg:
        idxs = np.argwhere(tot_num != 0).squeeze()
        succ = curr_samples[idxs] / tot_num[idxs]
        succ_rate[idxs] = succ

        curr_mean = np.mean(curr_samples)
        curr_rate = curr_mean - curr_samples
        curr_rate[curr_rate < 0] = 0
        if np.std(curr_rate) == 0:
            curr_rate = 1. / len(curr_rate)
        else:
            curr_rate = curr_rate / (1e-4 + np.sum(curr_rate))
        p = a*succ_rate + (1-a)*curr_rate
        p = p/(np.sum(p))

        selected = np.random.choice(range(len(curr_samples)), size=1, p=p)[0]
        (cls1, cls2) = index_dict[selected]
        data1_index = np.argwhere(predictions == cls1).squeeze()
        data2_index = np.argwhere(predictions == cls2).squeeze()
        conf1 = confs[data1_index]
        conf2 = confs[data2_index]

        # probability to be sampled is inversely proportional to the distance to "targeted" decision boundary
        # smaller class1-class2 is preferred
        pvec1 = (1 / (conf1[:, cls1] - conf1[:, cls2] + 1e-4)) / np.sum(
            (1 / (conf1[:, cls1] - conf1[:, cls2] + 1e-4)))
        pvec2 = (1 / (conf2[:, cls2] - conf2[:, cls1] + 1e-4)) / np.sum(
            (1 / (conf2[:, cls2] - conf2[:, cls1] + 1e-4)))

        image1_idx = np.random.choice(range(len(data1_index)), size=1, p=pvec1)
        image2_idx = np.random.choice(range(len(data2_index)), size=1, p=pvec2)

        image1 = input_x[data1_index[image1_idx]]
        image2 = input_x[data2_index[image2_idx]]

        attack1, successful1, _ = mixup_bi(model, image1, image2, cls1, cls2, device, l_bound=l_bound)
        if successful1:
            adv_examples = torch.cat((adv_examples, attack1), dim=0)
            num_adv += 1
            curr_samples[selected] += 1
            pbar.update(1)
        tot_num[selected] += 1

        if num_adv < num_adv_eg:
            attack2, successful2, _ = mixup_bi(model, image2, image1, cls2, cls1, device, l_bound=l_bound)
            tot_num[selected] += 1
            if successful2:
                adv_examples = torch.cat((adv_examples, attack2), dim=0)
                num_adv += 1
                curr_samples[selected] += 1
                pbar.update(1)
    
    pbar.close()
    t1 = time.time()
    if verbose:
        print('Total time {:2f}'.format(t1 - t0))

    return adv_examples, curr_samples, tot_num


# def batch_run(model, data, batch_size=200):
#     """batch run, in case memory error"""
#     data = data.to(dtype=torch.float)
#     output = None
#     n_batches = max(math.ceil(len(data) / batch_size), 1)
#     for b in tqdm.tqdm(range(n_batches)):
#         r1, r2 = b * batch_size, (b + 1) * batch_size
#         inputs = data[r1:r2]
#         with torch.no_grad():
#             pred = model(inputs).cpu().numpy()
#             if output is None:
#                 output = pred
#             else:
#                 output = np.concatenate((output, pred), axis=0)
#     return output

def batch_run(model, data, verbose=1, batch_size=200):
    """batch run, in case memory error"""
    data = data.to(dtype=torch.float)
    output = None
    n_batches = max(math.ceil(len(data) / batch_size), 1)
    if verbose == 1:
        for b in tqdm.tqdm(range(n_batches)):
            r1, r2 = b * batch_size, (b + 1) * batch_size
            inputs = data[r1:r2]
            with torch.no_grad():
                pred = model(inputs).cpu().numpy()
                if output is None:
                    output = pred
                else:
                    output = np.concatenate((output, pred), axis=0)
    else:
        for b in range(n_batches):
            r1, r2 = b * batch_size, (b + 1) * batch_size
            inputs = data[r1:r2]
            with torch.no_grad():
                pred = model(inputs).cpu().numpy()
                if output is None:
                    output = pred
                else:
                    output = np.concatenate((output, pred), axis=0)

    return output

def batch_run_tensor(model, data, device, verbose=1, batch_size=200):
    """batch run, in case memory error"""
    data = data.to(dtype=torch.float).to(device).requires_grad_(True)
    output = None

    pred = model(data)
    output = pred.clone()
    # n_batches = max(math.ceil(len(data) / batch_size), 1)

    # if verbose == 1:
    #     for b in tqdm.tqdm(range(n_batches)):
    #         r1, r2 = b * batch_size, (b + 1) * batch_size
    #         inputs = data[r1:r2]
    #         pred = model(inputs)  # 保持为 tensor，不转换为 numpy
    #         if output is None:
    #             output = pred.clone()
    #         else:
    #             output = torch.cat((output, pred), dim=0).clone()   # 使用 torch.cat 拼接 tensor
    # else:
    #     for b in range(n_batches):
    #         r1, r2 = b * batch_size, (b + 1) * batch_size
    #         inputs = data[r1:r2]
    #         print("Before fc:", inputs.requires_grad, inputs.grad_fn, inputs._version)  # 检查 x 是否具有 requires_grad 和 grad_fn
    #         pred = model(inputs)  # 保持为 tensor，不转换为 numpy
    #         print("After fc:", inputs.requires_grad, inputs.grad_fn, inputs._version)
    #         if output is None:
    #             output = pred.clone()
    #         else:
    #             output = torch.cat((output, pred), dim=0).clone()   # 使用 torch.cat 拼接 tensor

    #         # print("After cat:", output.requires_grad, output.grad_fn)

    return output  # 返回张量，且保持在指定的 device 上

def load_labelled_data_index(filename):
    with open(filename, 'r') as f:
        index = json.load(f)
    return index


def jaccard_similarity(l1, l2):
    u = np.union1d(l1,l2)
    i = np.intersect1d(l1,l2)
    return float(len(i)) / len(u)

def knn(data, k):
    # number of trees in random projection forest
    n_trees = min(64, 5 + int(round((data.shape[0]) ** 0.5 / 20.0)))
    # max number of nearest neighbor iters to perform
    n_iters = max(5, int(round(np.log2(data.shape[0]))))
    # distance metric
    metric = "euclidean"
    # get nearest neighbors
    nnd = NNDescent(
        data,
        n_neighbors=k,
        metric=metric,
        n_trees=n_trees,
        n_iters=n_iters,
        max_candidates=60,
        verbose=True
    )
    knn_indices, knn_dists = nnd.neighbor_graph
    return knn_indices, knn_dists


def hausdorff_dist(X, subset_idxs, metric="euclidean", verbose=1):
    '''
    Calculate the hausdorff distance of X and its subset
    '''
    t_s = time.time()
    tree = KDTree(X[subset_idxs], metric=metric)
    knn_dists, _ = tree.query(X, k=1)
    hausdorff = knn_dists[:, 0].max()
    t_e = time.time()
    if verbose>0:
        print("Calculate hausdorff distance {:.2f} for {:d}/{:d} in {:.3f} seconds...".format(hausdorff, len(subset_idxs),len(X), t_e-t_s))
    return hausdorff, round(t_e-t_s,3)


def hausdorff_dist_cus(X, subset_idxs, metric="euclidean", verbose=1):
    t_s = time.time()
    dist = pairwise_distances(X, X[subset_idxs], metric=metric)
    min_distances = np.min(dist, axis=1).reshape(-1,1)
    hausdorff = np.max(min_distances)
    t_e = time.time()
    if verbose > 0:
        print("Hausdorff distance {:.2f} for {:d}/{:d} in {:.3f} seconds...".format(hausdorff, len(subset_idxs),len(X), t_e-t_s))
        print(f'mean min_dist:\t{np.mean(min_distances)}')
    return hausdorff, round(t_e-t_s,3)


def is_B(preds):
    """
    given N points' prediction (N, class_num), we evaluate whether they are \delta-boundary points or not

    Please check the formal definition of \delta-boundary from our paper DVI
    :param preds: ndarray, (N, class_num), the output of model prediction before softmax layer
    :return: ndarray, (N:bool,),
    """

    preds = preds + 1e-8

    sort_preds = np.sort(preds)
    diff = (sort_preds[:, -1] - sort_preds[:, -2]) / (sort_preds[:, -1] - sort_preds[:, 0])

    is_border = np.zeros(len(diff), dtype=np.bool)
    is_border[diff < 0.1] = 1
    return is_border
    

def find_nearest(query):
    """
    find the distance to the nearest neighbor in the pool
    :param query: ndarray, shape (N,dim) 
    :param pool: ndarray (N, dim)
    :return dists: ndarray (N,)
    """
    # number of trees in random projection forest
    n_trees = min(64, 5 + int(round((query.shape[0]) ** 0.5 / 20.0)))
    # max number of nearest neighbor iters to perform
    n_iters = max(5, int(round(np.log2(query.shape[0]))))
    # distance metric
    metric = "euclidean"

    # get nearest neighbors
    nnd = NNDescent(
        query,
        n_neighbors=2,
        metric=metric,
        n_trees=n_trees,
        n_iters=n_iters,
        max_candidates=60,
        verbose=False
    )
    indices, distances = nnd.neighbor_graph
    return indices[:, 1], distances[:, 1]


def find_neighbor_preserving_rate(prev_data, train_data, n_neighbors):
    """
    neighbor preserving rate, (0, 1)
    :param prev_data: ndarray, shape(N,2) low dimensional embedding from last epoch
    :param train_data: ndarray, shape(N,2) low dimensional embedding from current epoch
    :param n_neighbors:
    :return alpha: ndarray, shape (N,)
    """
    if prev_data is None:
        return np.zeros(len(train_data))
    # number of trees in random projection forest
    n_trees = min(64, 5 + int(round((train_data.shape[0]) ** 0.5 / 20.0)))
    # max number of nearest neighbor iters to perform
    n_iters = max(5, int(round(np.log2(train_data.shape[0]))))
    # distance metric
    from pynndescent import NNDescent
    # get nearest neighbors
    nnd = NNDescent(
        train_data,
        n_neighbors=n_neighbors,
        metric="euclidean",
        n_trees=n_trees,
        n_iters=n_iters,
        max_candidates=60,
        verbose=True
    )
    train_indices, _ = nnd.neighbor_graph
    prev_nnd = NNDescent(
        prev_data,
        n_neighbors=n_neighbors,
        metric="euclidean",
        n_trees=n_trees,
        n_iters=n_iters,
        max_candidates=60,
        verbose=True
    )
    prev_indices, _ = prev_nnd.neighbor_graph
    temporal_pres = np.zeros(len(train_data))
    for i in range(len(train_indices)):
        pres = np.intersect1d(train_indices[i], prev_indices[i])
        temporal_pres[i] = len(pres) / float(n_neighbors)
    return temporal_pres


def kl_div(p, q):
    return stats.entropy(p, q, base=2)


def js_div(p, q):
    M = (p+q)/2
    return .5*kl_div(p, M)+.5*kl_div(q, M)

def generate_random_trajectory(x_min, y_min, x_max, y_max, period):
    xs = np.random.uniform(low=x_min, high=x_max, size=period)
    ys = np.random.uniform(low=y_min, high=y_max, size=period)
    trajectory = np.vstack((xs,ys)).transpose([1, 0])
    return trajectory

def generate_random_trajectory_momentum(init_position, period, alpha, gamma, vx, vy):
    xs = np.ones(period)
    ys = np.ones(period)
    xs[0] = init_position[0]
    ys[0] = init_position[1]
    for i in range(1, period):
        v_sample = np.zeros(2)
        v_sample[0] = np.random.normal(vx[i-1], 5, 1)[0]
        v_sample[1] = np.random.normal(vy[i-1], 5, 1)[0]
        # normalize
        history_direction = np.array([xs[i-1]-xs[0], ys[i-1]-ys[0]])
        if i > 1:
            history_direction = history_direction/np.linalg.norm(history_direction)*np.linalg.norm(v_sample)
        v = gamma*v_sample+alpha*history_direction
        xs[i] = xs[i-1] + v[0]
        ys[i] = ys[i-1] + v[1]
    return np.vstack((xs,ys)).transpose([1, 0])
    # return xs, ys

def ranking_dist(a,b):  
    n = len(a)
    assert len(b) == n
    i, j = np.meshgrid(np.arange(n), np.arange(n))
    ndisordered = np.logical_or(np.logical_and(a[i] < a[j], b[i] > b[j]), np.logical_and(a[i] > a[j], b[i] < b[j])).sum()
    return ndisordered / (n * (n - 1))


# def get_confidence_error_pairs(data_provider, epoch, projector, vis,threshold=0.3,resolution=200,k_neibour=15,max_refine=5000):
#     train_data = data_provider.train_representation(epoch)
#     train_data = train_data.reshape(train_data.shape[0],train_data.shape[1])
#     pred = data_provider.get_pred(epoch, train_data)
#     pred_res = pred.argmax(axis=1)
#     sort_preds = np.sort(pred, axis=1)
#     diff = (sort_preds[:, -1] - sort_preds[:, -2]) / (sort_preds[:, -1] - sort_preds[:, 0])
#     print("get org pred")
#     emb = projector.batch_project(epoch,train_data)
#     inv = projector.batch_inverse(epoch, emb)
#     inv_pred = data_provider.get_pred(epoch, inv)
#     inv_pred_res = inv_pred.argmax(axis=1)
#     inv_sort_preds = np.sort(inv_pred, axis=1)
#     inv_diff = (inv_sort_preds[:, -1] - inv_sort_preds[:, -2]) / (inv_sort_preds[:, -1] - inv_sort_preds[:, 0])
#     print("get inv pred")
#     conf_error = []
#     diff_values=[]
#     for i in range(len(diff)):
#         abs_diff = abs(diff[i] - inv_diff[i])
#         if abs_diff > threshold or pred_res[i]!=inv_pred_res[i]:
#             conf_error.append(i)
#             diff_values.append((abs_diff, pred_res[i] != inv_pred_res[i]))
#     print("real conf_error number:",len(conf_error),resolution)
#     # Combine indices with their respective differences and whether prediction differed
#     indexed_diff = list(zip(conf_error, diff_values))
#     # Sort primarily by whether predictions are different (True first), then by differences in descending order
#     indexed_diff.sort(key=lambda x: (~x[1][1], -x[1][0]))
#     # Select the first max_refine if there are more than 5000 elements
#     if len(indexed_diff) > max_refine:
#         indexed_diff = indexed_diff[:max_refine]
     
#     cof_error_emb = emb[conf_error]
#     grids = vis.get_grid(epoch, resolution)
#     inv_grid = projector.batch_inverse(epoch, grids)
#     print("conf_error number:",len(conf_error),resolution)
#     """
#         for each conf_error point, we calculate its k nearest low dimensional grids as negative anchors
#     """
#     diff = cof_error_emb[:, np.newaxis, :] - grids[np.newaxis, :, :]
#     dist_squared = np.sum(diff**2, axis=2)
#     k = k_neibour # Number of nearest neighbors to find
#     # Find the indices of the 10 nearest neighbors for each sample in cof_error_emb
#     nearest_neighbor_indices = np.argpartition(dist_squared, k, axis=1)[:, :k]
#     ##### os the 2d embedding's nearest 15 neibour should be used as negative samples
#     neg_grids = grids[nearest_neighbor_indices]
#     print("negative pairs:",neg_grids.shape)
    
#     """
#         for each conf_error point, we calculate its k nearest high dimensional grids as positive anchors
#     """
#     org_data = train_data[conf_error]
#     # from sklearn.neighbors import NearestNeighbors
#     high_neigh = NearestNeighbors(n_neighbors=k_neibour, radius=0.4)
#     high_neigh.fit(inv_grid)
#     _, knn_indices = high_neigh.kneighbors(org_data, n_neighbors=15, return_distance=True)
#     pos_grids = grids[knn_indices]
#     print("positive pairs:",pos_grids.shape)
    
#     return conf_error, neg_grids, pos_grids


def get_confidence_error_pairs(data_provider, epoch, projector, vis,threshold=0.3,resolution=200,k_neibour=15,max_refine=5000, high_r=1000):
    train_data = data_provider.train_representation(epoch)
    train_data = train_data.reshape(train_data.shape[0],train_data.shape[1])
    pred = data_provider.get_pred(epoch, train_data)
    pred_res = pred.argmax(axis=1)
    sort_preds = np.sort(pred, axis=1)
    diff = (sort_preds[:, -1] - sort_preds[:, -2]) / (sort_preds[:, -1] - sort_preds[:, 0])
    # print("get org pred")
    emb = projector.batch_project(epoch,train_data)
    inv = projector.batch_inverse(epoch, emb)
    inv_pred = data_provider.get_pred(epoch, inv)
    inv_pred_res = inv_pred.argmax(axis=1)
    inv_sort_preds = np.sort(inv_pred, axis=1)
    inv_diff = (inv_sort_preds[:, -1] - inv_sort_preds[:, -2]) / (inv_sort_preds[:, -1] - inv_sort_preds[:, 0])
    # print("get inv pred")
    conf_error = []
    diff_values=[]
    for i in range(len(diff)):
        abs_diff = abs(diff[i] - inv_diff[i])
        if abs_diff > threshold or pred_res[i]!=inv_pred_res[i]:
            conf_error.append(i)
            diff_values.append((abs_diff, pred_res[i] != inv_pred_res[i]))
    print("real conf_error number:",len(conf_error),resolution)
    # Combine indices with their respective differences and whether prediction differed
    indexed_diff = list(zip(conf_error, diff_values))
    # Sort primarily by whether predictions are different (True first), then by differences in descending order
    # Sort primarily by whether predictions are different (True first, use ~ to invert for sorting), then by differences in descending order
    indexed_diff.sort(key=lambda x: (~x[1][1], -x[1][0]))

    # Select the first max_refine if there are more than 5000 elements
    if len(indexed_diff) > max_refine:
        indexed_diff = indexed_diff[:max_refine]
     
    cof_error_emb = emb[conf_error]
    grids = vis.get_grid(epoch, resolution)
    a,b,c,d=vis.get_epoch_plot_measures(epoch)
    print("a,b,c,d",a,b,c,d)
    inv_grid = projector.batch_inverse(epoch, grids)
    # print("conf_error number:",len(conf_error),resolution)
    """
        for each conf_error point, we calculate its k nearest low dimensional grids as negative anchors
    """
    diff = cof_error_emb[:, np.newaxis, :] - grids[np.newaxis, :, :]
    dist_squared = np.sum(diff**2, axis=2)
    k = k_neibour # Number of nearest neighbors to find
    # Find the indices of the 10 nearest neighbors for each sample in cof_error_emb
    nearest_neighbor_indices = np.argpartition(dist_squared, k, axis=1)[:, :k]
    ##### os the 2d embedding's nearest 15 neibour should be used as negative samples
    neg_grids = grids[nearest_neighbor_indices]
    print("negative pairs:",neg_grids.shape)
    """
        for each conf_error point, we calculate its k nearest high dimensional grids as positive anchors
    """
    # inv_grid_pred = data_provider.get_pred(epoch, inv_grid)
    org_data = train_data[conf_error]
    # pred_for_conf_err = pred[conf_error]
    # from sklearn.neighbors import NearestNeighbors
    high_neigh = NearestNeighbors(n_neighbors=15, radius=0.4)
    high_neigh_ = NearestNeighbors(n_neighbors=15, radius=0.4)
    high_neigh_.fit(org_data)
    high_neigh.fit(inv_grid)
    _, knn_indices = high_neigh.kneighbors(org_data, n_neighbors=high_r, return_distance=True)
    _, knn_indices_ = high_neigh_.kneighbors(org_data, n_neighbors=2, return_distance=True)
    nearest_grids = grids[knn_indices] #### 2d grids sampe nearest neibour  N * 15 * 2
    org_emb_ = emb[knn_indices_] #### 2d training sampe nearest neibour  N * 1 * 2
    pos_grids = []
    distance_list = []
    for i in range(len(conf_error)):
        min_distance = float('inf')
        min_point =None
        train_point = org_emb_[i][1] # #TODO 2d point
        grid_N_emb = nearest_grids[i] # 15*2
        for grid_point in grid_N_emb:
            distance = np.linalg.norm(train_point - grid_point)
            if distance < min_distance:
                min_distance = distance
                min_point = grid_point
        distance_list.append(min_distance)
        pos_grids.append([min_point])
        
        
    pos_grids = np.array(pos_grids)   
    print("positive pairs:",pos_grids.shape)
    return conf_error, neg_grids, pos_grids,distance_list

    