from abc import ABC, abstractmethod
import os
import json

import numpy as np
from scipy import stats
from sklearn.neighbors import NearestNeighbors
from scipy.spatial.distance import cosine

from singleVis.eval.evaluate import *
from singleVis.backend import *
from singleVis.utils import is_B, js_div
from singleVis.visualizer import visualizer
import torch.nn.functional as F
from matplotlib.path import Path
from scipy.spatial import cKDTree

class EvaluatorAbstractClass(ABC):
    def __init__(self, data_provider, projector, *args, **kwargs):
        self.data_provider = data_provider
        self.projector = projector
    
    @abstractmethod
    def eval_nn_train(self, epoch, n_neighbors):
        pass

    @abstractmethod
    def eval_nn_test(self, epoch, n_neighbors):
        pass

    @abstractmethod
    def eval_inv_train(self, epoch):
        pass

    @abstractmethod
    def eval_inv_test(self, epoch):
        pass
    
    @abstractmethod
    def save_epoch_eval(self, n_epoch, file_name="evaluation"):
        pass

    @abstractmethod
    def get_eval(self, file_name="evaluation"):
        pass



class Evaluator(EvaluatorAbstractClass):
    def __init__(self, data_provider, projector, verbose=1):
        self.data_provider = data_provider
        self.projector = projector
        self.verbose = verbose

    ####################################### ATOM #############################################

    def eval_nn_train(self, epoch, n_neighbors):
        train_data = self.data_provider.train_representation(epoch)
        train_data = train_data.reshape(len(train_data), -1)
        embedding = self.projector.batch_project(epoch, train_data)
        val = evaluate_proj_nn_perseverance_knn(train_data, embedding, n_neighbors=n_neighbors, metric="euclidean")
        if self.verbose:
            print("#train# nn preserving: {:.2f}/{:d} in epoch {:d}".format(val, n_neighbors, epoch))
        return val

    def eval_nn_test(self, epoch, n_neighbors):
        train_data = self.data_provider.train_representation(epoch)
        train_data = train_data.reshape(len(train_data), -1)
        test_data = self.data_provider.test_representation(epoch)
        test_data = test_data.reshape(len(test_data), -1)
        fitting_data = np.concatenate((train_data, test_data), axis=0)
        embedding = self.projector.batch_project(epoch, fitting_data)
        val = evaluate_proj_nn_perseverance_knn(fitting_data, embedding, n_neighbors=n_neighbors, metric="euclidean")
        if self.verbose:
            print("#test# nn preserving : {:.2f}/{:d} in epoch {:d}".format(val, n_neighbors, epoch))
        return val

    def eval_b_train(self, epoch, n_neighbors, border=None):
        if border is not None:
            if border.size > 0:
                border_centers = border
        else:
            border_centers = self.data_provider.border_representation(epoch)
            border_centers = border_centers.reshape(len(border_centers), -1)

        train_data = self.data_provider.train_representation(epoch)
        train_data = train_data.reshape(len(train_data), -1)
       
        low_center = self.projector.batch_project(epoch, border_centers)
        low_train = self.projector.batch_project(epoch, train_data)

        val = evaluate_proj_boundary_perseverance_knn(train_data,
                                                      low_train,
                                                      border_centers,
                                                      low_center,
                                                      n_neighbors=n_neighbors)
        if self.verbose:
            print("#train# boundary preserving: {:.2f}/{:d} in epoch {:d}".format(val, n_neighbors, epoch))
        return val

    def eval_b_test(self, epoch, n_neighbors):
        test_data = self.data_provider.test_representation(epoch)
        test_data = test_data.reshape(len(test_data), -1)
        border_centers = self.data_provider.test_border_representation(epoch)
        border_centers = border_centers.reshape(len(border_centers), -1)

        low_center = self.projector.batch_project(epoch, border_centers)
        low_test = self.projector.batch_project(epoch, test_data)

        val = evaluate_proj_boundary_perseverance_knn(test_data,
                                                      low_test,
                                                      border_centers,
                                                      low_center,
                                                      n_neighbors=n_neighbors)
        if self.verbose:
            print("#test# boundary preserving: {:.2f}/{:d} in epoch {:d}".format(val, n_neighbors, epoch))
        return val

    def eval_inv_train(self, epoch):
        train_data = self.data_provider.train_representation(epoch)
        train_data = train_data.reshape(train_data.shape[0],train_data.shape[1])
        embedding = self.projector.batch_project(epoch, train_data)
        inv_data = self.projector.batch_inverse(epoch, embedding)

        pred = self.data_provider.get_pred(epoch, train_data).argmax(axis=1)
        new_pred = self.data_provider.get_pred(epoch, inv_data).argmax(axis=1)

        val = evaluate_inv_accu(pred, new_pred)
        val_error = evaluate_vis_error(pred, new_pred)
        if self.verbose:
            print("#train# PPR: {:.2f} in epoch {:d}, vis error:{:d}".format(val, epoch, val_error))
        return val,val_error

    def eval_inv_test(self, epoch):
        test_data = self.data_provider.test_representation(epoch)
        test_data = test_data.reshape(test_data.shape[0],test_data.shape[1])
        embedding = self.projector.batch_project(epoch, test_data)
        inv_data = self.projector.batch_inverse(epoch, embedding)

        pred = self.data_provider.get_pred(epoch, test_data).argmax(axis=1)
        new_pred = self.data_provider.get_pred(epoch, inv_data).argmax(axis=1)

        val = evaluate_inv_accu(pred, new_pred)
        val_error = evaluate_vis_error(pred, new_pred)
        if self.verbose:
            print("#test# PPR: {:.2f} in epoch {:d}, vis error:{:d}".format(val, epoch, val_error))
        return val,val_error
    
    
    def eval_inv_conf_train(self, epoch):
        train_data = self.data_provider.train_representation(epoch)
        train_data = train_data.reshape(train_data.shape[0],train_data.shape[1])
        embedding = self.projector.batch_project(epoch, train_data)
        inv_data = self.projector.batch_inverse(epoch, embedding)

        pred = self.data_provider.get_pred(epoch, train_data)
        new_pred = self.data_provider.get_pred(epoch, inv_data)

        train_pred_prob = compute_pred_confidence_diff(pred)
        inv_train_pred_prob = compute_pred_confidence_diff(new_pred)
        train_biased_samples = detect_confidence_bias(pred, new_pred, train_pred_prob, inv_train_pred_prob)
        train_biased_rate = len(train_biased_samples) / len(train_data)
        if self.verbose:
            print("#train# CPR: {:.2f} in epoch {:d}, conf error:{:d}".format((1-train_biased_rate), epoch, len(train_biased_samples)))
        return train_biased_rate, train_biased_samples
    
    def eval_inv_conf_test(self, epoch):
        test_data = self.data_provider.test_representation(epoch)
        test_data = test_data.reshape(test_data.shape[0],test_data.shape[1])
        embedding = self.projector.batch_project(epoch, test_data)
        inv_data = self.projector.batch_inverse(epoch, embedding)

        pred = self.data_provider.get_pred(epoch, test_data)
        new_pred = self.data_provider.get_pred(epoch, inv_data)

        train_pred_prob = compute_pred_confidence_diff(pred)
        inv_train_pred_prob = compute_pred_confidence_diff(new_pred)
        train_biased_samples = detect_confidence_bias(pred, new_pred, train_pred_prob, inv_train_pred_prob)
        train_biased_rate = len(train_biased_samples) / len(test_data)
        if self.verbose:
            print("#train# CPR: {:.2f} in epoch {:d}, conf error:{:d}".format((1-train_biased_rate), epoch, len(train_biased_samples)))
        return train_biased_rate, train_biased_samples    
    
    def eval_projection(self, logits, train_2d, all_nodes_tensor, node_to_pixels, batch_size=2000):
        """
        分批计算投影准确率，减少内存占用。
        :param logits: 训练数据集的预测logits (n_samples, n_classes)
        :param train_2d: 训练数据集的2D坐标 (n_samples, 2)
        :param all_nodes_tensor: 所有节点的10维表征 (n_nodes, 10)
        :param node_to_pixels: 每个表征对应的二维像素点范围的字典 {node_idx: pixel_points}
        :param batch_size: 每批处理的样本数量
        :return: 投影准确率和总误差
        """
        total_count = train_2d.shape[0]
        correct_projection_count = 0

        # 分批处理数据
        for i in range(0, total_count, batch_size):
            # 取当前 batch 的数据
            logits_batch = logits[i:i + batch_size]
            train_2d_batch = train_2d[i:i + batch_size]

            # 1. 计算当前 batch 的 softmax
            softmax_probs = F.softmax(logits_batch, dim=1)  # (batch_size, n_classes)

            # 2. 计算与 all_nodes_tensor 的余弦相似度
            cosine_sim = F.cosine_similarity(softmax_probs.unsqueeze(1), all_nodes_tensor.unsqueeze(0), dim=2)

            # 3. 找到最相似的 all_nodes_tensor
            closest_node_indices = torch.argmax(cosine_sim, dim=1).cpu().numpy()  # (batch_size,)

            # 4. 判断当前点的二维坐标是否落在对应的范围内
            for j, idx in enumerate(closest_node_indices):
                if idx in node_to_pixels:
                    pixel_points = node_to_pixels[idx]
                    # 判断当前 2D 点是否在 pixel_points 对应的区域内
                    if self._is_in_polygon(train_2d_batch[j], pixel_points):
                        correct_projection_count += 1

        # 5. 计算投影准确率和误差
        projection_accuracy = correct_projection_count / total_count
        projection_error = total_count - correct_projection_count

        print(f"Projection Accuracy: {projection_accuracy:.4f}, Projection Error: {projection_error}")

        return projection_accuracy, projection_error

    def _is_in_polygon(self, point, polygon):
        """
        判断二维坐标是否落在多边形区域内
        :param point: 2D 坐标 (2,)
        :param polygon: 由多个2D坐标点组成的多边形区域
        :return: 是否在多边形内
        """
        poly_path = Path(polygon)
        return poly_path.contains_point(point)
    
    def eval_projection_diff(self, logits, train_2d, all_nodes_tensor, all_nodes_2d, threshold=0.2, batch_size=2000):
        """
        计算每个训练点的二维坐标与 all_nodes_2d 中最近的点之间的差异，
        并判断 softmax_probs 与最近点对应的 all_nodes_tensor 的语义差异。
        
        :param train_2d: 训练数据集的2D坐标 (n_samples, 2)
        :param logits: 训练数据集的预测 logits (n_samples, n_classes)
        :param all_nodes_tensor: 所有节点的10维表征 (n_nodes, 10)
        :param all_nodes_2d: 节点的2D坐标 (n_nodes, 2)
        :param threshold: 阈值，判断差异是否超过该阈值
        :param batch_size: 每批处理的样本数量
        :return: 投影准确率和总误差
        """
        total_count = train_2d.shape[0]
        correct_projection_count = 0

        # 创建 cKDTree 以找到 train_2d 中点的最近邻
        tree = cKDTree(all_nodes_2d)

        # 分批处理数据
        for i in range(0, total_count, batch_size):
            # 取当前 batch 的数据
            train_2d_batch = train_2d[i:i + batch_size]
            logits_batch = logits[i:i + batch_size]

            # 1. 计算当前 batch 的 softmax
            softmax_probs = F.softmax(logits_batch, dim=1)  # (batch_size, n_classes)

            # 检查 train_2d_batch 是否是 Tensor
            if isinstance(train_2d_batch, torch.Tensor):
                train_2d_batch = train_2d_batch.detach().cpu().numpy()

            # 2. 查找每个 2D 坐标的最近邻节点
            distances, closest_node_indices = tree.query(train_2d_batch)  # 找到最近的 all_nodes_2d 中的点

            # 3. 根据最近邻节点找到对应的 all_nodes_tensor
            closest_nodes_tensor = all_nodes_tensor[closest_node_indices]

            # 4. 计算 softmax_probs 和对应的 all_nodes_tensor 的预测语义差异
            train_pred_prob = compute_pred_confidence_diff(softmax_probs.cpu().detach().numpy())
            inv_train_pred_prob = compute_pred_confidence_diff(closest_nodes_tensor.cpu().detach().numpy())
            confidence_diff = np.abs(train_pred_prob - inv_train_pred_prob)

            # 计算 train_pred_prob 和 inv_train_pred_prob 的最大值索引
            train_max_idx = np.argmax(softmax_probs.cpu().detach().numpy(), axis=1)
            inv_train_max_idx = np.argmax(closest_nodes_tensor.cpu().detach().numpy(), axis=1)

            # 5. 判断置信度差异是否小于阈值，且最大值索引是否一致
            valid_confidence = confidence_diff < threshold
            valid_prediction = train_max_idx == inv_train_max_idx

            # 6. 两条限制共同满足
            valid_points = np.logical_and(valid_confidence, valid_prediction)

            # 统计满足条件的点数
            correct_projection_count += np.sum(valid_points)

        # 6. 计算投影准确率和误差
        projection_accuracy = correct_projection_count / total_count
        projection_error = total_count - correct_projection_count

        print(f"Projection Accuracy: {projection_accuracy:.4f}, Projection Error: {projection_error}")

        return projection_accuracy, projection_error
    
    def eval_inv_dist_train(self, epoch):
        train_data = self.data_provider.train_representation(epoch)
        embedding = self.projector.batch_project(epoch, train_data)
        inv_data = self.projector.batch_inverse(epoch, embedding)
        dist = np.linalg.norm(train_data-inv_data, axis=1).mean()
        
        if self.verbose:
            print("#train# inverse projection distance: {:.2f} in epoch {:d}".format(dist, epoch))
        return float(dist)

    def eval_inv_dist_test(self, epoch):
        test_data = self.data_provider.test_representation(epoch)
        embedding = self.projector.batch_project(epoch, test_data)
        inv_data = self.projector.batch_inverse(epoch, embedding)
        dist = np.linalg.norm(test_data-inv_data, axis=1).mean()
        if self.verbose:
            print("#test# inverse projection distance: {:.2f} in epoch {:d}".format(dist, epoch))
        return float(dist)

    def eval_temporal_train(self, n_neighbors):
        eval_num = (self.data_provider.e - self.data_provider.s) // self.data_provider.p
        l = self.data_provider.train_num

        alpha = np.zeros((eval_num, l))
        delta_x = np.zeros((eval_num, l))

        for t in range(eval_num):
            prev_data = self.data_provider.train_representation(t * self.data_provider.p + self.data_provider.s)
            prev_embedding = self.projector.batch_project(t * self.data_provider.p + self.data_provider.s, prev_data)

            curr_data = self.data_provider.train_representation((t+1) * self.data_provider.p + self.data_provider.s)
            curr_embedding = self.projector.batch_project((t+1) * self.data_provider.p + self.data_provider.s, curr_data)

            alpha_ = find_neighbor_preserving_rate(prev_data, curr_data, n_neighbors=n_neighbors)
            delta_x_ = np.linalg.norm(prev_embedding - curr_embedding, axis=1)

            alpha[t] = alpha_
            delta_x[t] = delta_x_

        val_corr, corr_std = evaluate_proj_temporal_perseverance_corr(alpha, delta_x)
        if self.verbose:
            print("Temporal preserving (train): {:.3f}\t std :{:.3f}".format(val_corr, corr_std))
        return val_corr, corr_std

    def eval_temporal_test(self, n_neighbors):
        eval_num = (self.data_provider.e - self.data_provider.s) // self.data_provider.p
        l = self.data_provider.train_num + self.data_provider.test_num

        alpha = np.zeros((eval_num, l))
        delta_x = np.zeros((eval_num, l))
        for t in range(eval_num):
            prev_data_test = self.data_provider.test_representation(t * self.data_provider.p + self.data_provider.s)
            prev_data_train = self.data_provider.train_representation(t * self.data_provider.p + self.data_provider.s)
            prev_data = np.concatenate((prev_data_train, prev_data_test), axis=0)
            prev_embedding = self.projector.batch_project(t * self.data_provider.p + self.data_provider.s, prev_data)

            curr_data_test = self.data_provider.test_representation((t+1) * self.data_provider.p + self.data_provider.s)
            curr_data_train = self.data_provider.train_representation((t+1) * self.data_provider.p + self.data_provider.s)
            curr_data = np.concatenate((curr_data_train, curr_data_test), axis=0)
            curr_embedding = self.projector.batch_project((t+1) * self.data_provider.p + self.data_provider.s, curr_data)

            alpha_ = find_neighbor_preserving_rate(prev_data, curr_data, n_neighbors=n_neighbors)
            delta_x_ = np.linalg.norm(prev_embedding - curr_embedding, axis=1)

            alpha[t] = alpha_
            delta_x[t] = delta_x_

        val_corr, corr_std = evaluate_proj_temporal_perseverance_corr(alpha, delta_x)
        if self.verbose:
            print("Temporal preserving (test): {:.3f}\t std:{:.3f}".format(val_corr, corr_std))
        return val_corr, corr_std
    
    def norm(self, x):
        exp_x = np.exp(x - np.max(x, axis=1, keepdims=True))
        return exp_x / exp_x.sum(axis=1, keepdims=True)
    
    def if_border(self, data):
        norm_preds = self.norm(data)

        sort_preds = np.sort(norm_preds, axis=1)
        diff = sort_preds[:, -1] - sort_preds[:, -2]
        border = np.zeros(len(diff), dtype=np.uint8) + 0.05
        border[diff < 0.15] = 1
            
        return border
    
    def critical_prediction_flip(self, ref_pred, tar_pred):
        critical_prediction_flip_list = []
        for i in range(len(ref_pred)):
            if ref_pred[i] != tar_pred[i]:
                critical_prediction_flip_list.append(i)
        return critical_prediction_flip_list
                
    def critical_border_flip(self, ref_data, tar_data):
        critical_border_flip_list = []

        ref_border_list = self.if_border(ref_data)
        tar_border_list = self.if_border(tar_data)
        for i in range(len(ref_border_list)):
            if ref_border_list[i] != tar_border_list[i]:
                critical_border_flip_list.append(i)
        return critical_border_flip_list

    def eval_critical_temporal_train(self, n_neighbors):
        eval_num = (self.data_provider.e - self.data_provider.s) // self.data_provider.p
        # l = self.data_provider.train_num

        # alpha = np.zeros((eval_num, l))
        # delta_x = np.zeros((eval_num, l))
        alpha = []
        delta_x = []


        for t in range(eval_num):
            prev_iteration = t * self.data_provider.p + self.data_provider.s
            cur_iteration = (t+1) * self.data_provider.p + self.data_provider.s
            prev_data = self.data_provider.train_representation(prev_iteration)
            prev_data = prev_data.reshape(prev_data.shape[0],prev_data.shape[1])
            prev_embedding = self.projector.batch_project(prev_iteration, prev_data)

            curr_data = self.data_provider.train_representation(cur_iteration)
            curr_data = curr_data.reshape(curr_data.shape[0],curr_data.shape[1])
            curr_embedding = self.projector.batch_project(cur_iteration, curr_data)

            pred_origin = self.data_provider.get_pred(prev_iteration, prev_data)
            pred = pred_origin.argmax(axis=1)

            embedding_ref = self.projector.batch_project(prev_iteration, prev_data)
            inv_ref_data = self.projector.batch_inverse(prev_iteration, embedding_ref)

            inv_pred_origin = self.data_provider.get_pred(prev_iteration, inv_ref_data)
            inv_pred = inv_pred_origin.argmax(axis=1)

            vis_error_list = []
            for i in range(len(pred)):
                if pred[i] != inv_pred[i]:
                    vis_error_list.append(i)

            embedding_tar = self.projector.batch_project(prev_iteration, curr_data)
            inv_tar_data = self.projector.batch_inverse(prev_iteration, embedding_tar)

            new_pred_origin = self.data_provider.get_pred(cur_iteration, curr_data)
            new_pred = new_pred_origin.argmax(axis=1)

            inv_new_pred_origin = self.data_provider.get_pred(cur_iteration, inv_tar_data)
            inv_new_pred = inv_new_pred_origin.argmax(axis=1)

            for i in range(len(pred)):
                if new_pred[i] != inv_new_pred[i]:
                    vis_error_list.append(i)

            high_dim_prediction_flip_list = self.critical_prediction_flip(pred, new_pred)
            high_dim_border_flip_list = self.critical_border_flip(pred_origin, new_pred_origin)

            critical_set = set(high_dim_prediction_flip_list).union(set(high_dim_border_flip_list))
            critical_list = list(critical_set.union(set(vis_error_list)))

            alpha_ = find_neighbor_preserving_rate(prev_data[critical_list], curr_data[critical_list], n_neighbors=n_neighbors)
            delta_x_ = np.linalg.norm(prev_embedding[critical_list] - curr_embedding[critical_list], axis=1)

            # alpha[t] = alpha_
            # delta_x[t] = delta_x_
            alpha.append(alpha_)
            delta_x.append(delta_x_)

        val_corr, corr_std = evaluate_critical_proj_temporal_perseverance_corr(alpha, delta_x)
        if self.verbose:
            print("Temporal preserving (train): {:.3f}\t std :{:.3f}".format(val_corr, corr_std))
        return val_corr, corr_std

    def eval_critical_temporal_test(self, n_neighbors):
        eval_num = (self.data_provider.e - self.data_provider.s) // self.data_provider.p
        # l = self.data_provider.train_num + self.data_provider.test_num

        # alpha = np.zeros((eval_num, l))
        # delta_x = np.zeros((eval_num, l))
        alpha = []
        delta_x = []

        for t in range(eval_num):
            prev_iteration = t * self.data_provider.p + self.data_provider.s
            cur_iteration = (t+1) * self.data_provider.p + self.data_provider.s

            prev_data_test = self.data_provider.test_representation(prev_iteration)
            prev_data_train = self.data_provider.train_representation(prev_iteration)
            prev_data_test = prev_data_test.reshape(prev_data_test.shape[0],prev_data_test.shape[1])
            prev_data_train = prev_data_train.reshape(prev_data_train.shape[0],prev_data_train.shape[1])
            prev_data = np.concatenate((prev_data_train, prev_data_test), axis=0)
            prev_embedding = self.projector.batch_project(prev_iteration, prev_data)

            curr_data_test = self.data_provider.test_representation(cur_iteration)
            curr_data_train = self.data_provider.train_representation(cur_iteration)
            curr_data_test = curr_data_test.reshape(curr_data_test.shape[0],curr_data_test.shape[1])
            curr_data_train = curr_data_train.reshape(curr_data_train.shape[0],curr_data_train.shape[1])
            curr_data = np.concatenate((curr_data_train, curr_data_test), axis=0)
            curr_embedding = self.projector.batch_project(cur_iteration, curr_data)

            pred_origin = self.data_provider.get_pred(prev_iteration, prev_data)
            pred = pred_origin.argmax(axis=1)

            embedding_ref = self.projector.batch_project(prev_iteration, prev_data)
            inv_ref_data = self.projector.batch_inverse(prev_iteration, embedding_ref)

            inv_pred_origin = self.data_provider.get_pred(prev_iteration, inv_ref_data)
            inv_pred = inv_pred_origin.argmax(axis=1)

            vis_error_list = []
            for i in range(len(pred)):
                if pred[i] != inv_pred[i]:
                    vis_error_list.append(i)

            embedding_tar = self.projector.batch_project(prev_iteration, curr_data)
            inv_tar_data = self.projector.batch_inverse(prev_iteration, embedding_tar)

            new_pred_origin = self.data_provider.get_pred(cur_iteration, curr_data)
            new_pred = new_pred_origin.argmax(axis=1)

            inv_new_pred_origin = self.data_provider.get_pred(cur_iteration, inv_tar_data)
            inv_new_pred = inv_new_pred_origin.argmax(axis=1)

            for i in range(len(pred)):
                if new_pred[i] != inv_new_pred[i]:
                    vis_error_list.append(i)

            high_dim_prediction_flip_list = self.critical_prediction_flip(pred, new_pred)
            high_dim_border_flip_list = self.critical_border_flip(pred_origin, new_pred_origin)

            critical_set = set(high_dim_prediction_flip_list).union(set(high_dim_border_flip_list))
            critical_list = list(critical_set.union(set(vis_error_list)))

            alpha_ = find_neighbor_preserving_rate(prev_data[critical_list], curr_data[critical_list], n_neighbors=n_neighbors)
            delta_x_ = np.linalg.norm(prev_embedding[critical_list] - curr_embedding[critical_list], axis=1)

            # alpha[t] = alpha_
            # delta_x[t] = delta_x_
            alpha.append(alpha_)
            delta_x.append(delta_x_)

        val_corr, corr_std = evaluate_critical_proj_temporal_perseverance_corr(alpha, delta_x)
        if self.verbose:
            print("Temporal preserving (test): {:.3f}\t std:{:.3f}".format(val_corr, corr_std))
        return val_corr, corr_std

    def eval_temporal_nn_train(self, epoch, n_neighbors):
        epoch_num = (self.data_provider.e - self.data_provider.s) // self.data_provider.p + 1
        l = self.data_provider.train_num
        high_dists = np.zeros((l, epoch_num))
        low_dists = np.zeros((l, epoch_num))

        curr_data = self.data_provider.train_representation(epoch)
        curr_embedding = self.projector.batch_project(epoch, curr_data)
        
        for t in range(epoch_num):
            data = self.data_provider.train_representation(t * self.data_provider.p + self.data_provider.s)
            embedding = self.projector.batch_project(t * self.data_provider.p + self.data_provider.s, data)

            high_dist = np.linalg.norm(curr_data - data, axis=1)
            low_dist = np.linalg.norm(curr_embedding - embedding, axis=1)
            high_dists[:, t] = high_dist
            low_dists[:, t] = low_dist
        
        # find the index of top k dists
        # argsort descent order
        high_orders = np.argsort(high_dists, axis=1)
        low_orders = np.argsort(low_dists, axis=1)

        high_rankings = high_orders[:, 1:n_neighbors+1]
        low_rankings = low_orders[:, 1:n_neighbors+1]
        
        corr = np.zeros(len(high_dists))
        for i in range(len(data)):
            corr[i] = len(np.intersect1d(high_rankings[i], low_rankings[i]))

        if self.verbose:
            print("Temporal temporal neighbor preserving (train) for {}-th epoch {}: {:.3f}\t std :{:.3f}".format(epoch, n_neighbors, corr.mean(), corr.std()))
        return float(corr.mean())

    def eval_temporal_nn_test(self, epoch, n_neighbors):
        epoch_num = (self.data_provider.e - self.data_provider.s) // self.data_provider.p + 1
        l = self.data_provider.test_num
        high_dists = np.zeros((l, epoch_num))
        low_dists = np.zeros((l, epoch_num))

        curr_data = self.data_provider.test_representation(epoch)
        curr_embedding = self.projector.batch_project(epoch, curr_data)

        for t in range(epoch_num):
            data = self.data_provider.test_representation(t * self.data_provider.p + self.data_provider.s)
            embedding = self.projector.batch_project(t * self.data_provider.p + self.data_provider.s, data)

            high_dist = np.linalg.norm(curr_data - data, axis=1)
            low_dist = np.linalg.norm(curr_embedding - embedding, axis=1)
            high_dists[:, t] = high_dist
            low_dists[:,t] = low_dist
        
        # find the index of top k dists
        high_orders = np.argsort(high_dists, axis=1)
        low_orders = np.argsort(low_dists, axis=1)
        
        high_rankings = high_orders[:, 1:n_neighbors+1]
        low_rankings = low_orders[:, 1:n_neighbors+1]
        corr = np.zeros(len(high_dists))
        for i in range(len(data)):
            corr[i] = len(np.intersect1d(high_rankings[i], low_rankings[i]))

        if self.verbose:
            print("Temporal nn preserving (test) for {}-th epoch {}: {:.3f}\t std:{:.3f}".format(epoch, n_neighbors, corr.mean(), corr.std()))
        return float(corr.mean())

    def eval_spatial_temporal_nn_train(self, n_neighbors, feature_dim):
        """
            evaluate whether vis model can preserve the ranking of close spatial and temporal neighbors
        """
        #TODO: scale up to 100 epochs, need to speed up the process...
        epoch_num = (self.data_provider.e - self.data_provider.s) // self.data_provider.p + 1
        train_num = self.data_provider.train_num

        high_features = np.zeros((epoch_num*train_num, feature_dim))
        low_features = np.zeros((epoch_num*train_num, 2))

        for t in range(epoch_num):
            data = self.data_provider.train_representation(t * self.data_provider.p + self.data_provider.s)
            high_features[t*train_num:(t+1)*train_num] = np.copy(data)
            low_features[t*train_num:(t+1)*train_num] = self.projector.batch_project(t * self.data_provider.p + self.data_provider.s, data)
        
        val = evaluate_proj_nn_perseverance_knn(high_features, low_features, n_neighbors)

        if self.verbose:
            print("Spatial/Temporal nn preserving (train):\t{:.3f}/{:d}".format(val, n_neighbors))
        return val


    def eval_spatial_temporal_nn_test(self, n_neighbors, feature_dim):
        # find n temporal neighbors
        epoch_num = (self.data_provider.e - self.data_provider.s) // self.data_provider.p + 1
        train_num = self.data_provider.train_num
        test_num = self.data_provider.test_num
        num = train_num + test_num

        high_features = np.zeros((epoch_num*num, feature_dim))
        low_features = np.zeros((epoch_num*num, 2))

        for t in range(epoch_num):
            train_data = self.data_provider.train_representation(t * self.data_provider.p + self.data_provider.s)
            test_data = self.data_provider.test_representation(t * self.data_provider.p + self.data_provider.s)
            data = np.concatenate((train_data, test_data), axis=0)
            low_features[t*num:(t+1)*num] = self.projector.batch_project(t * self.data_provider.p + self.data_provider.s, data)
            high_features[t*num:(t+1)*num] = np.copy(data)

        val =evaluate_proj_nn_perseverance_knn(high_features, low_features, n_neighbors)
    
        if self.verbose:
            print("Spatial/Temporal nn preserving (test):\t{:.3f}/{:d}".format(val, n_neighbors))
        return val

    
    def eval_temporal_global_corr_train(self, epoch, start=None, end=None, period=None):
        # check if we use the default value
        if start is None:
            start = self.data_provider.s
            end = self.data_provider.e
            period = self.data_provider.p
        # set parameters
        LEN = self.data_provider.train_num
        EPOCH = (end - start) // period + 1
        repr_dim = self.data_provider.representation_dim
        all_train_repr = np.zeros((EPOCH,LEN,repr_dim))
        low_repr = np.zeros((EPOCH,LEN,2))

        # save all representation vectors
        for i in range(start,end + 1, period):
            index = (i - start) //  period
            all_train_repr[index] = self.data_provider.train_representation(i)
            low_repr[index] = self.projector.batch_project(i, all_train_repr[index])
        
        corrs = np.zeros(LEN)
        ps = np.zeros(LEN)
        for i in range(LEN):
            high_embeddings = all_train_repr[:,i,:].squeeze()
            low_embeddings = low_repr[:,i,:].squeeze()

            high_dists = np.linalg.norm(high_embeddings - high_embeddings[(epoch - start) //  period], axis=1)
            low_dists = np.linalg.norm(low_embeddings - low_embeddings[(epoch - start) //  period], axis=1)
        
            corr, p = stats.spearmanr(high_dists, low_dists)
            corrs[i] = corr
            ps[i] = p
        return corrs.mean()
    
    def eval_temporal_global_corr_test(self, epoch, start=None, end=None, period=None):
        # check if we use the default value
        if start is None:
            start = self.data_provider.s
            end = self.data_provider.e
            period = self.data_provider.p
        TEST_LEN = self.data_provider.test_num
        EPOCH = (end - start) // period + 1
        repr_dim = self.data_provider.representation_dim

        all_test_repr = np.zeros((EPOCH,TEST_LEN,repr_dim))
        low_repr = np.zeros((EPOCH,TEST_LEN,2))
        for i in range(start,end + 1, period):
            index = (i - start) //  period
            all_test_repr[index] = self.data_provider.test_representation(i)
            low_repr[index] = self.projector.batch_project(i, all_test_repr[index])

        corrs = np.zeros(TEST_LEN)
        ps = np.zeros(TEST_LEN)
        e = (epoch - start) // period
        for i in range(TEST_LEN):
            high_embeddings = all_test_repr[:,i,:].squeeze()
            low_embeddings = low_repr[:,i,:].squeeze()
            high_dists = np.linalg.norm(high_embeddings - high_embeddings[e], axis=1)
            low_dists = np.linalg.norm(low_embeddings - low_embeddings[e], axis=1)
            corr, p = stats.spearmanr(high_dists, low_dists)
            corrs[i] = corr
            ps[i] = p
        return corrs.mean()
    
    def eval_temporal_weighted_global_corr_train(self, epoch, start=None, end=None, period=None):
        # check if we use the default value
        if start is None:
            start = self.data_provider.s
            end = self.data_provider.e
            period = self.data_provider.p
        # set parameters
        LEN = self.data_provider.train_num
        EPOCH = (end - start) // period + 1
        repr_dim = self.data_provider.representation_dim
        all_train_repr = np.zeros((EPOCH,LEN,repr_dim))
        low_repr = np.zeros((EPOCH,LEN,2))

        # save all representation vectors
        for i in range(start,end + 1, period):
            index = (i - start) //  period
            all_train_repr[index] = self.data_provider.train_representation(i)
            low_repr[index] = self.projector.batch_project(i, all_train_repr[index])
        
        corrs = np.zeros(LEN)
        for i in range(LEN):
            high_embeddings = all_train_repr[:,i,:].squeeze()
            low_embeddings = low_repr[:,i,:].squeeze()

            high_dists = np.linalg.norm(high_embeddings - high_embeddings[(epoch - start) //  period], axis=1)
            low_dists = np.linalg.norm(low_embeddings - low_embeddings[(epoch - start) //  period], axis=1)
            
            high_ranking = np.argsort(high_dists)
            low_ranking = np.argsort(low_dists)
            
            corr = evaluate_proj_temporal_weighted_global_corr(high_ranking, low_ranking)
            corrs[i] = corr
        return corrs.mean()
    
    def eval_temporal_weighted_global_corr_test(self, epoch, start=None, end=None, period=None):
        # check if we use the default value
        if start is None:
            start = self.data_provider.s
            end = self.data_provider.e
            period = self.data_provider.p
        TEST_LEN = self.data_provider.test_num
        EPOCH = (end - start) // period + 1
        repr_dim = self.data_provider.representation_dim

        all_test_repr = np.zeros((EPOCH,TEST_LEN,repr_dim))
        low_repr = np.zeros((EPOCH,TEST_LEN,2))
        for i in range(start,end + 1, period):
            index = (i - start) //  period
            all_test_repr[index] = self.data_provider.test_representation(i)
            low_repr[index] = self.projector.batch_project(i, all_test_repr[index])

        corrs = np.zeros(TEST_LEN)
        e = (epoch - start) // period
        for i in range(TEST_LEN):
            high_embeddings = all_test_repr[:,i,:].squeeze()
            low_embeddings = low_repr[:,i,:].squeeze()
            high_dists = np.linalg.norm(high_embeddings - high_embeddings[e], axis=1)
            low_dists = np.linalg.norm(low_embeddings - low_embeddings[e], axis=1)
            high_ranking = np.argsort(high_dists)
            low_ranking = np.argsort(low_dists)
            corr = evaluate_proj_temporal_weighted_global_corr(high_ranking, low_ranking)
            corrs[i] = corr
        return corrs.mean()

    
    def eval_temporal_local_corr_train(self, epoch, stage, start=None, end=None, period=None):
        # check if we use the default value
        if start is None:
            start = self.data_provider.s
            end = self.data_provider.e
            period = self.data_provider.p
        timeline = np.arange(start, end+period, period)
        # divide into several stages
        stage_idxs =  np.array_split(timeline, stage)
        selected_stage = stage_idxs[np.where([epoch in i for i in stage_idxs])[0][0]]

        # set parameters
        LEN = self.data_provider.train_num
        EPOCH = len(selected_stage)
        repr_dim = self.data_provider.representation_dim
        all_train_repr = np.zeros((EPOCH,LEN,repr_dim))
        low_repr = np.zeros((EPOCH,LEN,2))

        s = selected_stage[0]

        # save all representation vectors
        for i in selected_stage:
            index = (i - s) //  period
            all_train_repr[index] = self.data_provider.train_representation(i)
            low_repr[index] = self.projector.batch_project(i, all_train_repr[index])
        
        corrs = np.zeros(LEN)
        for i in range(LEN):
            high_embeddings = all_train_repr[:,i,:]
            low_embeddings = low_repr[:,i,:]

            high_dists = np.linalg.norm(high_embeddings - high_embeddings[(epoch - s) //  period], axis=1)
            low_dists = np.linalg.norm(low_embeddings - low_embeddings[(epoch - s) //  period], axis=1)
            corr, _ = stats.spearmanr(high_dists, low_dists)
            corrs[i] = corr
        return corrs.mean()
    
    def eval_temporal_local_corr_test(self, epoch, stage, start=None, end=None, period=None):
        # check if we use the default value
        if start is None:
            start = self.data_provider.s
            end = self.data_provider.e
            period = self.data_provider.p
        
        timeline = np.arange(start, end+period, period)
        # divide into several stages
        stage_idxs =  np.array_split(timeline, stage)
        selected_stage = stage_idxs[np.where([epoch in i for i in stage_idxs])[0][0]]
        s=selected_stage[0]

        TEST_LEN = self.data_provider.test_num
        EPOCH = len(selected_stage)
        repr_dim = self.data_provider.representation_dim

        all_test_repr = np.zeros((EPOCH,TEST_LEN,repr_dim))
        low_repr = np.zeros((EPOCH,TEST_LEN,2))
        for i in selected_stage:
            index = (i-s)//period
            all_test_repr[index] = self.data_provider.test_representation(i)
            low_repr[index] = self.projector.batch_project(i, all_test_repr[index])

        corrs = np.zeros(TEST_LEN)
        e = (epoch - s) // period
        for i in range(TEST_LEN):
            high_embeddings = all_test_repr[:,i,:]
            low_embeddings = low_repr[:,i,:]
            high_dists = np.linalg.norm(high_embeddings - high_embeddings[e], axis=1)
            low_dists = np.linalg.norm(low_embeddings - low_embeddings[e], axis=1)
            corr, _ = stats.spearmanr(high_dists, low_dists)
            corrs[i] = corr
        return corrs.mean()
    
    def eval_moving_invariants_train(self, e_s, e_t, resolution=500):

        train_data_s = self.data_provider.train_representation(e_s)
        train_data_t = self.data_provider.train_representation(e_t)

        pred_s = self.data_provider.get_pred(e_s, train_data_s)
        pred_t = self.data_provider.get_pred(e_t, train_data_t)

        low_s = self.projector.batch_project(e_s, train_data_s)
        low_t = self.projector.batch_project(e_t, train_data_t)

        s_B = is_B(pred_s)
        t_B = is_B(pred_t)

        predictions_s = pred_s.argmax(1)
        predictions_t = pred_t.argmax(1)

        # TODO implement more case where loss is not cross entropy
        confident_sample = np.logical_and(np.logical_not(s_B),np.logical_not(t_B))
        diff_pred = predictions_s!=predictions_t

        # select confident and moving samples
        selected = np.logical_and(diff_pred, confident_sample)

        # background related
        vis = visualizer(self.data_provider, self.projector, resolution, cmap='tab10')
        grid_view_s, _ = vis.get_epoch_decision_view(e_s, resolution)
        grid_view_t, _ = vis.get_epoch_decision_view(e_t, resolution)

        grid_view_s = grid_view_s.reshape(resolution*resolution, -1)
        grid_view_t = grid_view_t.reshape(resolution*resolution, -1)

        grid_samples_s = self.projector.batch_inverse(e_s, grid_view_s)
        grid_samples_t = self.projector.batch_inverse(e_t, grid_view_t)

        grid_pred_s = self.data_provider.get_pred(e_s, grid_samples_s)+1e-8
        grid_pred_t = self.data_provider.get_pred(e_t, grid_samples_t)+1e-8
        
        grid_s_B = is_B(grid_pred_s)
        grid_t_B = is_B(grid_pred_t)

        grid_predictions_s = grid_pred_s.argmax(1)
        grid_predictions_t = grid_pred_t.argmax(1)

        # find nearest grid samples
        high_neigh = NearestNeighbors(n_neighbors=1, radius=0.4)
        high_neigh.fit(grid_view_s)
        _, knn_indices = high_neigh.kneighbors(low_s, n_neighbors=1, return_distance=True)

        close_s_pred = grid_predictions_s[knn_indices].squeeze()
        close_s_B = grid_s_B[knn_indices].squeeze()
        s_true = np.logical_and(close_s_pred==predictions_s, close_s_B == s_B)
        
        high_neigh = NearestNeighbors(n_neighbors=1, radius=0.4)
        high_neigh.fit(grid_view_t)
        _, knn_indices = high_neigh.kneighbors(low_t, n_neighbors=1, return_distance=True)

        close_t_pred = grid_predictions_t[knn_indices].squeeze()
        close_t_B = grid_t_B[knn_indices].squeeze()
        t_true = np.logical_and(close_t_pred==predictions_t, close_t_B == t_B)

        moving_sample_num = np.sum(selected)
        true_num = np.sum(np.logical_and(s_true[selected], t_true[selected]))
        print(f'moving invariant Low/High:\t{true_num}/{moving_sample_num}')

        return true_num, moving_sample_num
    

    def eval_moving_invariants_test(self, e_s, e_t, resolution=500):
        test_data_s = self.data_provider.test_representation(e_s)
        test_data_t = self.data_provider.test_representation(e_t)

        pred_s = self.data_provider.get_pred(e_s, test_data_s)
        pred_t = self.data_provider.get_pred(e_t, test_data_t)

        low_s = self.projector.batch_project(e_s, test_data_s)
        low_t = self.projector.batch_project(e_t, test_data_t)

        s_B = is_B(pred_s)
        t_B = is_B(pred_t)

        predictions_s = pred_s.argmax(1)
        predictions_t = pred_t.argmax(1)

        confident_sample = np.logical_and(np.logical_not(s_B),np.logical_not(t_B))
        diff_pred = predictions_s!=predictions_t

        selected = np.logical_and(diff_pred, confident_sample)

        # background related
        vis = visualizer(self.data_provider, self.projector, resolution, cmap='tab10')
        grid_view_s, _ = vis.get_epoch_decision_view(e_s, resolution)
        grid_view_t, _ = vis.get_epoch_decision_view(e_t, resolution)

        grid_view_s = grid_view_s.reshape(resolution*resolution, -1)
        grid_view_t = grid_view_t.reshape(resolution*resolution, -1)

        grid_samples_s = self.projector.batch_inverse(e_s, grid_view_s)
        grid_samples_t = self.projector.batch_inverse(e_t, grid_view_t)

        grid_pred_s = self.data_provider.get_pred(e_s, grid_samples_s)+1e-8
        grid_pred_t = self.data_provider.get_pred(e_t, grid_samples_t)+1e-8
        
        grid_s_B = is_B(grid_pred_s)
        grid_t_B = is_B(grid_pred_t)

        grid_predictions_s = grid_pred_s.argmax(1)
        grid_predictions_t = grid_pred_t.argmax(1)

        # find nearest grid samples
        high_neigh = NearestNeighbors(n_neighbors=1, radius=0.4)
        high_neigh.fit(grid_view_s)
        _, knn_indices = high_neigh.kneighbors(low_s, n_neighbors=1, return_distance=True)

        close_s_pred = grid_predictions_s[knn_indices].squeeze()
        close_s_B = grid_s_B[knn_indices].squeeze()
        s_true = np.logical_and(close_s_pred==predictions_s, close_s_B == s_B)
        

        high_neigh = NearestNeighbors(n_neighbors=1, radius=0.4)
        high_neigh.fit(grid_view_t)
        _, knn_indices = high_neigh.kneighbors(low_t, n_neighbors=1, return_distance=True)

        close_t_pred = grid_predictions_t[knn_indices].squeeze()
        close_t_B = grid_t_B[knn_indices].squeeze()
        t_true = np.logical_and(close_t_pred==predictions_t, close_t_B == t_B)

        moving_sample_num = np.sum(selected)
        true_num = np.sum(np.logical_and(s_true[selected], t_true[selected]))
        print(f'moving invariant Low/High:\t{true_num}/{moving_sample_num}')

        return true_num, moving_sample_num
    
    def eval_fixing_invariants_train(self, e_s, e_t, high_threshold, low_threshold, metric="euclidean"):
        train_data_s = self.data_provider.train_representation(e_s)
        train_data_t = self.data_provider.train_representation(e_t)

        # _, high_threshold = find_nearest(train_data_s)
        pred_s = self.data_provider.get_pred(e_s, train_data_s)
        pred_t = self.data_provider.get_pred(e_t, train_data_t)
        softmax_s = softmax(pred_s, axis=1)
        softmax_t = softmax(pred_t, axis=1)

        low_s = self.projector.batch_project(e_s, train_data_s)
        low_t = self.projector.batch_project(e_t, train_data_t)

        # normalize low_t
        y_max = max(low_s[:, 1].max(), low_t[:, 1].max())
        y_min = max(low_s[:, 1].min(), low_t[:, 1].min())
        x_max = max(low_s[:, 0].max(), low_t[:, 0].max())
        x_min = max(low_s[:, 0].min(), low_t[:, 0].min())
        scale = min(100/(x_max - x_min), 100/(y_max - y_min))
        low_t = low_t*scale
        low_s = low_s*scale

        if metric == "euclidean":
            high_dists = np.linalg.norm(train_data_s-train_data_t, axis=1)
        elif metric == "cosine":
            high_dists = np.array([cosine(low_t[i], low_s[i]) for i in range(len(low_s))])
        elif metric == "softmax":
            high_dists = np.array([js_div(softmax_s[i], softmax_t[i]) for i in range(len(softmax_t))])
        low_dists = np.linalg.norm(low_s-low_t, axis=1)

        selected = high_dists<=high_threshold

        return np.sum(np.logical_and(selected, low_dists<=low_threshold)), np.sum(selected)

    def eval_fixing_invariants_test(self, e_s, e_t, high_threshold, low_threshold, metric="euclidean"):
        test_data_s = self.data_provider.test_representation(e_s)
        test_data_t = self.data_provider.test_representation(e_t)

        # _, high_threshold = find_nearest(test_data_s)
        pred_s = self.data_provider.get_pred(e_s, test_data_s)
        pred_t = self.data_provider.get_pred(e_t, test_data_t)
        softmax_s = softmax(pred_s, axis=1)
        softmax_t = softmax(pred_t, axis=1)

        low_s = self.projector.batch_project(e_s, test_data_s)
        low_t = self.projector.batch_project(e_t, test_data_t)

        # normalize low_t
        y_max = max(low_s[:, 1].max(), low_t[:, 1].max())
        y_min = max(low_s[:, 1].min(), low_t[:, 1].min())
        x_max = max(low_s[:, 0].max(), low_t[:, 0].max())
        x_min = max(low_s[:, 0].min(), low_t[:, 0].min())
        scale = min(100/(x_max - x_min), 100/(y_max - y_min))
        low_t = low_t*scale
        low_s = low_s*scale

        if metric == "euclidean":
            high_dists = np.linalg.norm(test_data_s-test_data_t, axis=1)
        elif metric == "cosine":
            high_dists = np.array([cosine(low_t[i], low_s[i]) for i in range(len(low_s))])
        elif metric == "softmax":
            high_dists = np.array([js_div(softmax_s[i], softmax_t[i]) for i in range(len(softmax_t))])
        low_dists = np.linalg.norm(low_s-low_t, axis=1)

        selected = high_dists<=high_threshold

        return np.sum(np.logical_and(selected, low_dists<=low_threshold)), np.sum(selected)
    
    def eval_proj_invariants_train(self, e, resolution=500):
        train_data = self.data_provider.train_representation(e)
        pred_s = self.data_provider.get_pred(e, train_data)
        low_s = self.projector.batch_project(e, train_data)
        s_B = is_B(pred_s)
        predictions_s = pred_s.argmax(1)

        # background related
        vis = visualizer(self.data_provider, self.projector, resolution, cmap='tab10')
        grid_view_s, _ = vis.get_epoch_decision_view(e, resolution)
        grid_view_s = grid_view_s.reshape(resolution*resolution, -1)
        grid_samples_s = self.projector.batch_inverse(e, grid_view_s)
        grid_pred_s = self.data_provider.get_pred(e, grid_samples_s)+1e-8
        grid_s_B = is_B(grid_pred_s)
        grid_predictions_s = grid_pred_s.argmax(1)

        # find nearest grid samples
        high_neigh = NearestNeighbors(n_neighbors=1, radius=0.4)
        high_neigh.fit(grid_view_s)
        _, knn_indices = high_neigh.kneighbors(low_s, n_neighbors=1, return_distance=True)

        close_s_pred = grid_predictions_s[knn_indices].squeeze()
        close_s_B = grid_s_B[knn_indices].squeeze()

        border_true = np.logical_and(s_B, close_s_B)
        pred_true = np.logical_and(close_s_pred==predictions_s, np.logical_not(s_B))

        print("border fixing invariants:\t{}/{}".format(np.sum(border_true), np.sum(s_B)))
        print("prediction fixing invariants:\t{}/{}".format(np.sum(pred_true), np.sum(np.logical_not(s_B))))
        print("invariants:\t{}/{}".format(np.sum(border_true)+np.sum(pred_true), len(train_data)))

        
        return np.sum(border_true), np.sum(pred_true), len(train_data)
    
    def eval_proj_invariants_test(self, e, resolution=500):
        test_data = self.data_provider.test_representation(e)
        pred_s = self.data_provider.get_pred(e, test_data)
        low_s = self.projector.batch_project(e, test_data)
        s_B = is_B(pred_s)
        predictions_s = pred_s.argmax(1)

        # background related
        vis = visualizer(self.data_provider, self.projector, resolution, cmap='tab10')
        grid_view_s, _ = vis.get_epoch_decision_view(e, resolution)
        grid_view_s = grid_view_s.reshape(resolution*resolution, -1)
        grid_samples_s = self.projector.batch_inverse(e, grid_view_s)
        grid_pred_s = self.data_provider.get_pred(e, grid_samples_s)+1e-8
        grid_s_B = is_B(grid_pred_s)
        grid_predictions_s = grid_pred_s.argmax(1)

        # find nearest grid samples
        high_neigh = NearestNeighbors(n_neighbors=1, radius=0.4)
        high_neigh.fit(grid_view_s)
        _, knn_indices = high_neigh.kneighbors(low_s, n_neighbors=1, return_distance=True)

        close_s_pred = grid_predictions_s[knn_indices].squeeze()
        close_s_B = grid_s_B[knn_indices].squeeze()

        border_true = np.logical_and(s_B, close_s_B)
        pred_true = np.logical_and(close_s_pred==predictions_s, np.logical_not(s_B))

        print("border fixing invariants:\t{}/{}".format(np.sum(border_true), np.sum(s_B)))
        print("prediction fixing invariants:\t{}/{}".format(np.sum(pred_true), np.sum(np.logical_not(s_B))))
        print("invariants:\t{}/{}".format(np.sum(border_true)+np.sum(pred_true), len(test_data)))

        return np.sum(border_true), np.sum(pred_true), len(test_data)
    
    def train_acc(self, epoch):
        data = self.data_provider.train_representation(epoch)
        data = data.reshape(data.shape[0], data.shape[1])
        labels = self.data_provider.train_labels(epoch)
        pred = self.data_provider.get_pred(epoch, data).argmax(1)
        return np.sum(labels==pred)/len(labels)
    
    def test_acc(self, epoch):
        data = self.data_provider.test_representation(epoch)
        data = data.reshape(data.shape[0], data.shape[1])
        labels = self.data_provider.test_labels(epoch)
        pred = self.data_provider.get_pred(epoch, data).argmax(1)
        return np.sum(labels==pred)/len(labels)

    #################################### helper functions #############################################
    def save_epoch_eval_for_subject_model(self, n_epoch, file_name="evaluation"):
        # save result
        save_dir = os.path.join(self.data_provider.model_path)
        save_file = os.path.join(save_dir, file_name + ".json")
        if not os.path.exists(save_file):
            evaluation = dict()
        else:
            f = open(save_file, "r")
            evaluation = json.load(f)
            f.close()


        if "train_acc" not in evaluation.keys():
            evaluation["train_acc"] = dict()
        if "test_acc" not in evaluation.keys():
            evaluation["test_acc"] = dict()
        
        epoch_key = str(n_epoch)
        evaluation["train_acc"][epoch_key] = self.train_acc(n_epoch)
        evaluation["test_acc"][epoch_key] = self.test_acc(n_epoch)


        with open(save_file, "w") as f:
            json.dump(evaluation, f)
      

    def save_epoch_eval(self, n_epoch, n_neighbors, temporal_k=5, file_name="evaluation"):
        # save result
        save_dir = os.path.join(self.data_provider.model_path)
        save_file = os.path.join(save_dir, file_name + ".json")
        if not os.path.exists(save_file):
            evaluation = dict()
        else:
            f = open(save_file, "r")
            evaluation = json.load(f)
            f.close()
        n_key = str(n_neighbors)

        if "train_acc" not in evaluation.keys():
            evaluation["train_acc"] = dict()
        if "test_acc" not in evaluation.keys():
            evaluation["test_acc"] = dict()
        if "nn_train" not in evaluation:
            evaluation["nn_train"] = dict()
        if "nn_test" not in evaluation:
            evaluation["nn_test"] = dict()
        if "b_train" not in evaluation:
            evaluation["b_train"] = dict()
        if "b_test" not in evaluation:
            evaluation["b_test"] = dict()
        if "ppr_train" not in evaluation.keys():
            evaluation["ppr_train"] = dict()
        if "ppr_test" not in evaluation.keys():
            evaluation["ppr_test"] = dict()
        if "vis_error_train" not in evaluation.keys():
            evaluation["vis_error_train"] = dict()
        if "vis_error_test" not in evaluation.keys():
            evaluation["vis_error_test"] = dict()
        if "ppr_dist_train" not in evaluation.keys():
            evaluation["ppr_dist_train"] = dict()
        if "ppr_dist_test" not in evaluation.keys():
            evaluation["ppr_dist_test"] = dict()
        if "tnn_train" not in evaluation.keys():
            evaluation["tnn_train"] = dict()
        if "tnn_test" not in evaluation.keys():
            evaluation["tnn_test"] = dict()
        if "tr_train" not in evaluation.keys():
            evaluation["tr_train"] = dict()
        if "tr_test" not in evaluation.keys():
            evaluation["tr_test"] = dict()  
        if "wtr_train" not in evaluation.keys():
            evaluation["wtr_train"] = dict()
        if "wtr_test" not in evaluation.keys():
            evaluation["wtr_test"] = dict() 
        if "tlr_train" not in evaluation.keys():
            evaluation["tlr_train"] = dict()
        if "tlr_test" not in evaluation.keys():
            evaluation["tlr_test"] = dict()  
         

        if "temporal_train_mean" not in evaluation.keys():
            evaluation["temporal_train_mean"] = dict()
        if "temporal_test_mean" not in evaluation.keys():
            evaluation["temporal_test_mean"] = dict()

        epoch_key = str(n_epoch)
        if epoch_key not in evaluation["nn_train"]:
            evaluation["nn_train"][epoch_key] = dict()
        evaluation["nn_train"][epoch_key][n_key] = self.eval_nn_train(n_epoch, n_neighbors)
        if epoch_key not in evaluation["nn_test"]:
            evaluation["nn_test"][epoch_key] = dict()
        evaluation["nn_test"][epoch_key][n_key] = self.eval_nn_test(n_epoch, n_neighbors)
        # if epoch_key not in evaluation["b_train"]:
        #     evaluation["b_train"][epoch_key] = dict()
        # evaluation["b_train"][epoch_key][n_key] = self.eval_b_train(n_epoch, n_neighbors)
        # if epoch_key not in evaluation["b_test"]:
        #     evaluation["b_test"][epoch_key] = dict()
        # evaluation["b_test"][epoch_key][n_key] = self.eval_b_test(n_epoch, n_neighbors)
        if epoch_key not in evaluation["ppr_train"]:
            evaluation["ppr_train"][epoch_key] = dict()

        val, val_e = self.eval_inv_train(n_epoch)

        evaluation["ppr_train"][epoch_key] = val
        if epoch_key not in evaluation["vis_error_train"]:
            evaluation["vis_error_train"][epoch_key] = dict()
        evaluation["vis_error_train"][epoch_key] = int(val_e)

        if epoch_key not in evaluation["ppr_test"]:
            evaluation["ppr_test"][epoch_key] = dict()
        val, val_e = self.eval_inv_test(n_epoch)
        evaluation["ppr_test"][epoch_key] = val

        if epoch_key not in evaluation["vis_error_test"]:
            evaluation["vis_error_test"][epoch_key] = dict()
        evaluation["vis_error_test"][epoch_key] = int(val_e)

        # evaluation["ppr_dist_train"][epoch_key] = self.eval_inv_dist_train(n_epoch)
        # evaluation["ppr_dist_test"][epoch_key] = self.eval_inv_dist_test(n_epoch)

        evaluation["train_acc"][epoch_key] = self.train_acc(n_epoch)
        evaluation["test_acc"][epoch_key] = self.test_acc(n_epoch)

        # # local temporal
        # if epoch_key not in evaluation["tnn_train"].keys():
        #     evaluation["tnn_train"][epoch_key] = dict()
        # if epoch_key not in evaluation["tnn_test"].keys():
        #     evaluation["tnn_test"][epoch_key] = dict()
        # evaluation["tnn_train"][epoch_key][str(temporal_k)] = self.eval_temporal_nn_train(n_epoch, temporal_k)
        # evaluation["tnn_test"][epoch_key][str(temporal_k)] = self.eval_temporal_nn_test(n_epoch, temporal_k)

        # # global temporal ranking
        # evaluation["tr_train"][epoch_key] = self.eval_temporal_global_corr_train(n_epoch)
        # evaluation["tr_test"][epoch_key] = self.eval_temporal_global_corr_test(n_epoch)
        
        # weighted global temporal ranking
        # evaluation["wtr_train"][epoch_key] = self.eval_temporal_weighted_global_corr_train(n_epoch)
        # evaluation["wtr_test"][epoch_key] = self.eval_temporal_weighted_global_corr_test(n_epoch)

        # # local temporal ranking
        # evaluation["tlr_train"][epoch_key] = self.eval_temporal_local_corr_train(n_epoch, 3)
        # evaluation["tlr_test"][epoch_key] = self.eval_temporal_local_corr_test(n_epoch,3)

        # # temporal
        # t_train_val, _ = self.eval_temporal_train(n_neighbors)
        # evaluation["temporal_train_mean"][n_key] = t_train_val
        # t_test_val, _ = self.eval_temporal_test(n_neighbors)
        # evaluation["temporal_test_mean"][n_key] = t_test_val

        with open(save_file, "w") as f:
            json.dump(evaluation, f)
        if self.verbose:
            print("Successfully save evaluation with {:d} neighbors...".format(n_neighbors))
    
    def get_eval(self, file_name="evaluation"):
        save_dir = os.path.join(self.data_provider.model_path, file_name + ".json")
        f = open(save_dir, "r")
        evaluation = json.load(f)
        f.close()
        return evaluation


class SegEvaluator(Evaluator):
    def __init__(self, data_provider, projector, exp, verbose=1):
        super().__init__(data_provider, projector, verbose)
        self.exp = exp
    
    def save_epoch_eval(self, n_epoch, n_neighbors, temporal_k=5, file_name="evaluation"):
        # save result
        save_dir = os.path.join(self.data_provider.model_path, "{}".format(self.exp))
        save_file = os.path.join(save_dir, file_name + ".json")
        if not os.path.exists(save_file):
            evaluation = dict()
        else:
            f = open(save_file, "r")
            evaluation = json.load(f)
            f.close()
        n_key = str(n_neighbors)

        # if "train_acc" not in evaluation.keys():
        #     evaluation["train_acc"] = dict()
        # if "test_acc" not in evaluation.keys():
        #     evaluation["test_acc"] = dict()

        if "nn_train" not in evaluation:
            evaluation["nn_train"] = dict()
        if "nn_test" not in evaluation:
            evaluation["nn_test"] = dict()
        # if "b_train" not in evaluation:
        #     evaluation["b_train"] = dict()
        # if "b_test" not in evaluation:
        #     evaluation["b_test"] = dict()
        if "ppr_train" not in evaluation.keys():
            evaluation["ppr_train"] = dict()
        if "ppr_test" not in evaluation.keys():
            evaluation["ppr_test"] = dict()
        # if "tnn_train" not in evaluation.keys():
        #     evaluation["tnn_train"] = dict()
        # if "tnn_test" not in evaluation.keys():
        #     evaluation["tnn_test"] = dict()
        # if "tr_train" not in evaluation.keys():
        #     evaluation["tr_train"] = dict()
        # if "tr_test" not in evaluation.keys():
        #     evaluation["tr_test"] = dict()  
        if "tlr_train" not in evaluation.keys():
            evaluation["tlr_train"] = dict()
        if "tlr_test" not in evaluation.keys():
            evaluation["tlr_test"] = dict()  

        epoch_key = str(n_epoch)
        if epoch_key not in evaluation["nn_train"]:
            evaluation["nn_train"][epoch_key] = dict()
        evaluation["nn_train"][epoch_key][n_key] = self.eval_nn_train(n_epoch, n_neighbors)
        if epoch_key not in evaluation["nn_test"]:
            evaluation["nn_test"][epoch_key] = dict()
        evaluation["nn_test"][epoch_key][n_key] = self.eval_nn_test(n_epoch, n_neighbors)
        # if epoch_key not in evaluation["b_train"]:
        #     evaluation["b_train"][epoch_key] = dict()
        # evaluation["b_train"][epoch_key][n_key] = self.eval_b_train(n_epoch, n_neighbors)
        # if epoch_key not in evaluation["b_test"]:
        #     evaluation["b_test"][epoch_key] = dict()
        # evaluation["b_test"][epoch_key][n_key] = self.eval_b_test(n_epoch, n_neighbors)
        evaluation["ppr_train"][epoch_key] = self.eval_inv_train(n_epoch)
        evaluation["ppr_test"][epoch_key] = self.eval_inv_test(n_epoch)

        # local temporal
        # if epoch_key not in evaluation["tnn_train"].keys():
        #     evaluation["tnn_train"][epoch_key] = dict()
        # if epoch_key not in evaluation["tnn_test"].keys():
        #     evaluation["tnn_test"][epoch_key] = dict()
        # evaluation["tnn_train"][epoch_key][str(temporal_k)] = self.eval_temporal_nn_train(n_epoch, temporal_k)
        # evaluation["tnn_test"][epoch_key][str(temporal_k)] = self.eval_temporal_nn_test(n_epoch, temporal_k)

        # global ranking temporal
        # evaluation["tr_train"][epoch_key] = self.eval_temporal_global_corr_train(n_epoch)
        # evaluation["tr_test"][epoch_key] = self.eval_temporal_global_corr_test(n_epoch)

        # local ranking temporal
        evaluation["tlr_train"][epoch_key] = self.eval_temporal_local_corr_train(n_epoch, 3)
        evaluation["tlr_test"][epoch_key] = self.eval_temporal_local_corr_test(n_epoch, 3)

        # evaluation["train_acc"][epoch_key] = self.train_acc(n_epoch)
        # evaluation["test_acc"][epoch_key] = self.test_acc(n_epoch)

        # temporal
        # t_train_val, t_train_std = self.eval_temporal_train(n_neighbors)
        # evaluation[n_key]["temporal_train_mean"] = t_train_val
        # evaluation[n_key]["temporal_train_std"] = t_train_std
        # t_test_val, t_test_std = self.eval_temporal_test(n_neighbors)
        # evaluation[n_key]["temporal_test_mean"] = t_test_val
        # evaluation[n_key]["temporal_test_std"] = t_test_std

        with open(save_file, "w") as f:
            json.dump(evaluation, f)
        if self.verbose:
            print("Successfully save evaluation with {:d} neighbors...".format(n_neighbors))
    
    def get_eval(self, file_name="evaluation"):
        save_dir = os.path.join(self.data_provider.model_path, "{}".format(self.exp), file_name + ".json")
        f = open(save_dir, "r")
        evaluation = json.load(f)
        f.close()
        return evaluation

    
    def get_eval(self, file_name="evaluation"):
        save_dir = os.path.join(self.data_provider.model_path, file_name + ".json")
        f = open(save_dir, "r")
        evaluation = json.load(f)
        f.close()
        return evaluation


class SegEvaluator(Evaluator):
    def __init__(self, data_provider, projector, exp, verbose=1):
        super().__init__(data_provider, projector, verbose)
        self.exp = exp
    
    def save_epoch_eval(self, n_epoch, n_neighbors, temporal_k=5, file_name="evaluation"):
        # save result
        save_dir = os.path.join(self.data_provider.model_path, "{}".format(self.exp))
        save_file = os.path.join(save_dir, file_name + ".json")
        if not os.path.exists(save_file):
            evaluation = dict()
        else:
            f = open(save_file, "r")
            evaluation = json.load(f)
            f.close()
        n_key = str(n_neighbors)

        # if "train_acc" not in evaluation.keys():
        #     evaluation["train_acc"] = dict()
        # if "test_acc" not in evaluation.keys():
        #     evaluation["test_acc"] = dict()

        if "nn_train" not in evaluation:
            evaluation["nn_train"] = dict()
        if "nn_test" not in evaluation:
            evaluation["nn_test"] = dict()
        # if "b_train" not in evaluation:
        #     evaluation["b_train"] = dict()
        # if "b_test" not in evaluation:
        #     evaluation["b_test"] = dict()
        if "ppr_train" not in evaluation.keys():
            evaluation["ppr_train"] = dict()
        if "ppr_test" not in evaluation.keys():
            evaluation["ppr_test"] = dict()
        # if "tnn_train" not in evaluation.keys():
        #     evaluation["tnn_train"] = dict()
        # if "tnn_test" not in evaluation.keys():
        #     evaluation["tnn_test"] = dict()
        # if "tr_train" not in evaluation.keys():
        #     evaluation["tr_train"] = dict()
        # if "tr_test" not in evaluation.keys():
        #     evaluation["tr_test"] = dict()  
        if "tlr_train" not in evaluation.keys():
            evaluation["tlr_train"] = dict()
        if "tlr_test" not in evaluation.keys():
            evaluation["tlr_test"] = dict()  

        epoch_key = str(n_epoch)
        if epoch_key not in evaluation["nn_train"]:
            evaluation["nn_train"][epoch_key] = dict()
        evaluation["nn_train"][epoch_key][n_key] = self.eval_nn_train(n_epoch, n_neighbors)
        if epoch_key not in evaluation["nn_test"]:
            evaluation["nn_test"][epoch_key] = dict()
        evaluation["nn_test"][epoch_key][n_key] = self.eval_nn_test(n_epoch, n_neighbors)
        # if epoch_key not in evaluation["b_train"]:
        #     evaluation["b_train"][epoch_key] = dict()
        # evaluation["b_train"][epoch_key][n_key] = self.eval_b_train(n_epoch, n_neighbors)
        # if epoch_key not in evaluation["b_test"]:
        #     evaluation["b_test"][epoch_key] = dict()
        # evaluation["b_test"][epoch_key][n_key] = self.eval_b_test(n_epoch, n_neighbors)
        evaluation["ppr_train"][epoch_key] = self.eval_inv_train(n_epoch)
        evaluation["ppr_test"][epoch_key] = self.eval_inv_test(n_epoch)

        # local temporal
        # if epoch_key not in evaluation["tnn_train"].keys():
        #     evaluation["tnn_train"][epoch_key] = dict()
        # if epoch_key not in evaluation["tnn_test"].keys():
        #     evaluation["tnn_test"][epoch_key] = dict()
        # evaluation["tnn_train"][epoch_key][str(temporal_k)] = self.eval_temporal_nn_train(n_epoch, temporal_k)
        # evaluation["tnn_test"][epoch_key][str(temporal_k)] = self.eval_temporal_nn_test(n_epoch, temporal_k)

        # global ranking temporal
        # evaluation["tr_train"][epoch_key] = self.eval_temporal_global_corr_train(n_epoch)
        # evaluation["tr_test"][epoch_key] = self.eval_temporal_global_corr_test(n_epoch)

        # local ranking temporal
        evaluation["tlr_train"][epoch_key] = self.eval_temporal_local_corr_train(n_epoch, 3)
        evaluation["tlr_test"][epoch_key] = self.eval_temporal_local_corr_test(n_epoch, 3)

        # evaluation["train_acc"][epoch_key] = self.train_acc(n_epoch)
        # evaluation["test_acc"][epoch_key] = self.test_acc(n_epoch)

        # temporal
        # t_train_val, t_train_std = self.eval_temporal_train(n_neighbors)
        # evaluation[n_key]["temporal_train_mean"] = t_train_val
        # evaluation[n_key]["temporal_train_std"] = t_train_std
        # t_test_val, t_test_std = self.eval_temporal_test(n_neighbors)
        # evaluation[n_key]["temporal_test_mean"] = t_test_val
        # evaluation[n_key]["temporal_test_std"] = t_test_std

        with open(save_file, "w") as f:
            json.dump(evaluation, f)
        if self.verbose:
            print("Successfully save evaluation with {:d} neighbors...".format(n_neighbors))
    
    def get_eval(self, file_name="evaluation"):
        save_dir = os.path.join(self.data_provider.model_path, "{}".format(self.exp), file_name + ".json")
        f = open(save_dir, "r")
        evaluation = json.load(f)
        f.close()
        return evaluation

class ALEvaluator(Evaluator):
    def __init__(self, data_provider, projector, verbose=1):
        super().__init__(data_provider, projector, verbose)

    def train_acc(self, epoch):
        data = self.data_provider.train_representation(epoch)
        labels = self.data_provider.train_labels(epoch)
        pred = self.data_provider.get_pred(epoch, data).argmax(1)
        return np.sum(labels==pred)/len(labels)

    #################################### helper functions #############################################

    def save_epoch_eval(self, n_epoch, file_name="evaluation"):
        # save result
        save_dir = os.path.join(self.data_provider.model_path)
        save_file = os.path.join(save_dir, file_name + ".json")
        if not os.path.exists(save_file):
            evaluation = dict()
        else:
            f = open(save_file, "r")
            evaluation = json.load(f)
            f.close()
        if "train_acc" not in evaluation.keys():
            evaluation["train_acc"] = dict()
        if "test_acc" not in evaluation.keys():
            evaluation["test_acc"] = dict()
        epoch_key = str(n_epoch)

        evaluation["train_acc"][epoch_key] = self.train_acc(n_epoch)
        evaluation["test_acc"][epoch_key] = self.test_acc(n_epoch)

        with open(save_file, "w") as f:
            json.dump(evaluation, f)
        if self.verbose:
            print("Successfully save evaluation for Iteration {}".format(epoch_key))


class DenseALEvaluator(Evaluator):
    # TODO
    def __init__(self, data_provider, projector, verbose=1):
        super().__init__(data_provider, projector, verbose)

def softmax_(logits):
        exp_logits = np.exp(logits)
        return exp_logits / np.sum(exp_logits, axis=1, keepdims=True)

def detect_confidence_bias(train_pred, inv_train_pred, train_pred_prob, inv_train_pred_prob, threshold=0.2):
    """
    检测两个预测结果在最大置信度上的偏差。
    
    参数:
    - train_pred_prob: (ndarray) 预测1的置信度矩阵，形状为 (N, C)
    - inv_train_pred_prob: (ndarray) 预测2的置信度矩阵，形状为 (N, C)
    - threshold: (float) 判定为置信度偏差的差值阈值，默认为 0.1
    
    返回:
    - biased_samples: (ndarray) 存在偏差的样本索引
    """
    # 1. 找到每个样本的最大置信度及其对应的类别
    train_max_idx = np.argmax(train_pred, axis=1)  # (N,) 每个样本在 train_pred_prob 中置信度最大的类别

    inv_train_max_idx = np.argmax(inv_train_pred, axis=1)  # inv_train_pred_prob 中置信度最大的类别

    # 2. 判断两个预测是否来自同一个类别
    same_category = train_max_idx == inv_train_max_idx  # (N,) 布尔值，判断两个最大置信度是否来自同一个类别

    # 3. 判断两个置信度是否在给定的阈值以内
    confidence_diff = np.abs(train_pred_prob - inv_train_pred_prob)  # 计算置信度的差异
    confidence_in_range = confidence_diff <= threshold  # 布尔值，判断置信度差异是否在阈值以内

    # 4. 定义偏差：若不属于同一类别，或置信度差异大于阈值，则标记为偏差
    has_bias = ~same_category | ~confidence_in_range  # 布尔值，表示哪些样本有偏差

    # 5. 找到所有有偏差的样本索引
    biased_samples = np.where(has_bias)[0]

    return biased_samples

def compute_pred_confidence_diff(predictions):
    """
    计算每个样本分类结果的置信度差异，衡量模型对分类结果的确定性。

    Parameters:
    predictions (np.ndarray): 模型的预测结果，形状为 (num_samples, num_classes)，
                              其中每一行代表一个样本在不同类别上的预测概率。

    Returns:
    np.ndarray: 每个样本的置信度差异值，形状为 (num_samples,)。
    """
    # 避免除以零的数值不稳定问题
    predictions = predictions + 1e-8

    # 对预测结果进行排序
    sorted_preds = np.sort(predictions, axis=1)

    # 计算最大置信度和次大置信度的差异，并归一化
    pred_confidence_diff = (sorted_preds[:, -1] - sorted_preds[:, -2]) / (sorted_preds[:, -1] - sorted_preds[:, 0])

    return pred_confidence_diff