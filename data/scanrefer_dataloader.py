import os,sys
sys.path.append('../')
import time
import json
import pickle as p
import pickle
import numpy as np
from torch.utils.data import Dataset

from data.config import CONF
from data.model_util_scannet import random_sampling,rotx,roty,rotz,rotate_aligned_boxes_along_axis
import torch
import cv2
import MinkowskiEngine as ME
import glob
import copy

def load_axis_align_matrix(meta_file):
    lines = open(meta_file).readlines()
    for line in lines:
        if 'axisAlignment' in line:
            axis_align_matrix = [float(x) for x in line.rstrip().strip('axisAlignment = ').split(' ')]
            break
    axis_align_matrix=np.array(axis_align_matrix).reshape((4, 4))
    return axis_align_matrix

def load_intrinsic_matrix(meta_file):
    lines=open(meta_file).readlines()
    for line in lines:
        if "m_calibrationColorIntrinsic" in line:
            ColorIntrinsicMatrix=[float(x) for x in line.rstrip().strip('m_calibrationColorIntrinsic = ').split(" ")]
        if "m_calibrationDepthIntrinsic" in line:
            DepthIntrinsicMatrix=[float(x) for x in line.rstrip().strip("m_calibrationDepthIntrinsic = ").split(" ")]
    ColorIntrinsicMatrix=np.array(ColorIntrinsicMatrix).reshape((4,4))
    DepthIntrinsicMatrix=np.array(DepthIntrinsicMatrix).reshape((4,4))

    return ColorIntrinsicMatrix,DepthIntrinsicMatrix

class singleRGBD_dataset(Dataset):
    def __init__(self,
                 cfg,
                 isTrain=True,
                 num_points=20000,
                 use_color=True,
                 use_aug=True,
                 debug=False,
                 voxel_size=0.05,
                 sigma=0.5):
        self.sigma=sigma
        self.debug=debug
        self.voxel_size=voxel_size
        self.use_color=use_color
        self.num_points=num_points
        self.use_aug=use_aug
        self.isTrain=isTrain
        self.nyu40ids = np.array(
            [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 23, 24, 25, 26, 27, 28, 29, 30, 31,
             32, 33, 34, 35, 36, 37, 38, 39, 40])

        if self.isTrain:
            self.split_file=CONF.PATH.SRGBD_Train_Path
        else:
            self.split_file=CONF.PATH.SRGBD_Test_Path
        with open(self.split_file,'r') as f:
            self.scanrefer=json.load(f)

        self.__load_data()
    def __len__(self):
        return len(self.scanrefer)

    def get_axis_align_matrix(self,txt_file):
        lines = open(txt_file).readlines()
        for line in lines:
            if 'axisAlignment' in line:
                axis_align_matrix = [float(x) for x in line.rstrip().strip('axisAlignment = ').split(' ')]
                break
        axis_align_matrix = np.array(axis_align_matrix).reshape((4, 4))
        return axis_align_matrix

    def __getitem__(self,idx):
        scene_id=self.scanrefer[idx]['scene_id']
        sentence=self.scanrefer[idx]['description']
        object_id=int(self.scanrefer[idx]['object_id'])
        object_name=" ".join(self.scanrefer[idx]["object_name"].split("_"))
        ann_id = self.scanrefer[idx]["ann_id"]
        image_id_list = self.scanrefer[idx]['chosen_image_id']

        #-----------------------------load language feature-----------------------
        positive_mask = True
        lang_feat = self.lang[scene_id][str(object_id)][ann_id]
        lang_len = len(self.scanrefer[idx]["token"])
        lang_len = lang_len if lang_len <= CONF.TRAIN.MAX_DES_LEN else CONF.TRAIN.MAX_DES_LEN
        #-----------------------------load point cloud-----------------------------
        image_id=image_id_list[np.random.randint(0,len(image_id_list))]
        point_cloud_path=os.path.join(CONF.PATH.SRGBD_PCD_Path,scene_id,"pcd_%s_%s.npy"%(str(object_id),image_id))
        point_cloud=np.load(point_cloud_path)
        semantic_label=point_cloud[:,6]
        instance_label=point_cloud[:,7]

        bbox=self.scene_data[scene_id]['bbox'].copy()

        if not self.use_color:
            point_cloud=point_cloud[:,0:3]
        else:
            point_cloud=point_cloud[:,0:6]
            point_cloud[:,3:]=(point_cloud[:,3:]-CONF.MEAN_COLOR_RGB)/128.0 #normalize the point cloud between -1 to 1#

        #---------------------------------LABELS-------------------------------
        target_bboxes=np.zeros((CONF.MAX_NUM_OBJ,6))
        target_bboxes_mask=np.zeros((CONF.MAX_NUM_OBJ))

        point_cloud, choices=random_sampling(point_cloud,self.num_points,return_choices=True)
        semantic_label=semantic_label[choices]
        instance_label=instance_label[choices]
        num_bbox=bbox.shape[0] if bbox.shape[0]<CONF.MAX_NUM_OBJ else CONF.MAX_NUM_OBJ
        target_bboxes_mask[0:num_bbox]=1
        target_bboxes[0:num_bbox,:]=bbox[:num_bbox,0:6]

        # ------------------------------- DATA AUGMENTATION --------------------------
        if self.use_aug:
            if np.random.random()>0.5:
                # Flipping along the YZ plane
                point_cloud[:,0]= -1*point_cloud[:,0]
                target_bboxes[:,0]=-1*target_bboxes[:,0]
            if np.random.random()>0.5:
                # Flipping along the XZ plane
                point_cloud[:,1]=-1*point_cloud[:,1]
                target_bboxes[:,1]=-1*target_bboxes[:,1]

            # Rotation along X-axis
            rot_angle=(np.random.random()*np.pi/18)-np.pi/36# -5 ~ 5 degree
            rot_mat=rotx(rot_angle)
            point_cloud[:,0:3]=np.dot(point_cloud[:,0:3],np.transpose(rot_mat))
            target_bboxes = rotate_aligned_boxes_along_axis(target_bboxes,rot_mat,'x')

            # Rotation along Y-axis
            rot_angle = (np.random.random() * np.pi / 18) - np.pi / 36  # -5 ~ 5 degree
            rot_mat = roty(rot_angle)
            point_cloud[:, 0:3] = np.dot(point_cloud[:,0:3], np.transpose(rot_mat))
            target_bboxes = rotate_aligned_boxes_along_axis(target_bboxes, rot_mat, 'y')

            # Rotation along Z-axis
            rot_angle = (np.random.random() * np.pi / 18) - np.pi / 36  # -5 ~ 5 degree
            rot_mat = rotz(rot_angle)
            point_cloud[:, 0:3] = np.dot(point_cloud[:,0:3], np.transpose(rot_mat))
            target_bboxes = rotate_aligned_boxes_along_axis(target_bboxes, rot_mat, 'z')


            point_cloud,target_bboxes,coor_on_img=self._translate(point_cloud,target_bboxes,coor_on_img)

        # --------------------------generate partial bounding box for partial scan----------------------
        # print(target_bboxes.shape)
        ref_box_label = np.zeros(CONF.MAX_NUM_OBJ)
        for i, gt_id in enumerate(bbox[:num_bbox, -1]):  # -1 is the instance id
            if gt_id == object_id:
                ref_box_label[i] = 1  ## which bounding box is the correct box

        if self.use_partial_bbox:
            intact_bbox = target_bboxes[np.where(ref_box_label)[0], 0:6]
            intact_bbox = intact_bbox[0]

            object_pcd_ind=(point_cloud[:,0]<intact_bbox[0]+intact_bbox[3]/2)&(point_cloud[:,0]>intact_bbox[0]-intact_bbox[3]/2)&\
            (point_cloud[:, 1] < intact_bbox[1] + intact_bbox[4] / 2)&(point_cloud[:, 1] > intact_bbox[1] - intact_bbox[4] / 2)&\
            (point_cloud[:, 2] < intact_bbox[2] + intact_bbox[5] / 2)&(point_cloud[:, 2] > intact_bbox[2] - intact_bbox[5] / 2)
            object_pcd_ind=np.where(object_pcd_ind)[0]
            object_pcd_xyz=point_cloud[object_pcd_ind]
            object_instance=instance_label[object_pcd_ind]
            select_ind=np.where(object_instance==(object_id+1))[0]
            object_pcd_xyz=object_pcd_xyz[select_ind]
            if object_pcd_xyz.shape[0]>0:
                x_min = np.min(object_pcd_xyz[:, 0])
                y_min = np.min(object_pcd_xyz[:, 1])
                z_min = np.min(object_pcd_xyz[:, 2])

                x_max = np.max(object_pcd_xyz[:, 0])
                y_max = np.max(object_pcd_xyz[:, 1])
                z_max = np.max(object_pcd_xyz[:, 2])

                partial_bbox = np.array([(x_min + x_max) / 2,
                                         (y_min + y_max) / 2,
                                         (z_min + z_max) / 2,
                                         (x_max - x_min),
                                         (y_max - y_min),
                                         (z_max - z_min)])
            else:
                partial_bbox=intact_bbox
        #-----------------------------------------------------------------------------

        #generate ground truth vote for each object
        #this part is different from ScanRefer
        #we are using partial point cloud
        # and the ground truth vote comes from the bounding box
        point_votes = np.zeros([self.num_points, 3])
        point_votes_mask = np.zeros(self.num_points)
        ins2sem = self.scene_data[scene_id]["ins2sem"]
        for i_instance in np.unique(instance_label):
            if i_instance == 0:
                continue
            ind = np.where(instance_label == (i_instance))[0]
            # print(ind.shape)
            object_bbox = target_bboxes[np.where(bbox[:, -1] == (i_instance - 1))[0], 0:6]
            object_bbox = object_bbox[0]
            if ind.shape[0] > 0:
                # if semantic_label[ind[0]] in self.nyu40ids:
                if self.use_partial_bbox and ins2sem[str(int(i_instance))] in self.nyu40ids:
                    x = point_cloud[ind, :3]
                    valid_ind = (x[:, 0] < object_bbox[0] + object_bbox[3] / 2) & (
                            x[:, 0] > object_bbox[0] - object_bbox[3] / 2) & \
                                (x[:, 1] < object_bbox[1] + object_bbox[4] / 2) & (
                                        x[:, 1] > object_bbox[1] - object_bbox[4] / 2) & \
                                (x[:, 2] < object_bbox[2] + object_bbox[5] / 2) & (
                                        x[:, 2] > object_bbox[2] - object_bbox[5] / 2)
                    valid_ind = np.where(valid_ind)[0]
                    global_ind = ind[valid_ind]
                    if valid_ind.shape[0] > 0:
                        x = point_cloud[global_ind, 0:3]
                        center = 0.5 * (x.min(0) + x.max(0))
                        point_votes[global_ind, :] = center
                        point_votes_mask[global_ind] = 1.0

        #----------------------generate sparse voxel-------------------------------------------------

        dist=(point_cloud[:,0:3]-intact_bbox[0:3])**2
        x_std = intact_bbox[3]
        y_std = intact_bbox[4]
        z_std = intact_bbox[5]
        dist = dist / np.array([x_std ** 2, y_std ** 2, z_std ** 2])
        dist = np.sqrt(np.sum(dist, axis=1))
        gaussian_kernel = np.exp(-dist / 2 / self.sigma ** 2)
        atten_label = gaussian_kernel
        xyz = point_cloud[:, 0:3].copy()
        feats = point_cloud[:, 3:6].copy()
        coords = xyz / self.voxel_size
        batch={}

        batch["partial_gt_bbox"]=partial_bbox
        batch["intact_gt_bbox"]=intact_bbox
        batch["coords"]=coords
        batch["positive_mask"]=positive_mask
        batch["feats"]=feats
        batch["pcd2vox_coor"]=coords
        batch["atten_label"]=atten_label
        batch["input_point_cloud"]=point_cloud
        batch["lang_feat"]=lang_feat
        batch['lang_len']=lang_len
        batch['object_id']=object_id
        batch["all_target_bboxes"] = target_bboxes
        batch["target_bboxes_mask"] = target_bboxes_mask

        batch['ann_id']=ann_id
        batch['box_label']=ref_box_label
        batch['scene_id']=scene_id
        batch['object_id']=str(object_id)
        batch['box_label']=ref_box_label
        batch['point_votes']=point_votes
        batch['point_votes_mask']=point_votes_mask
        batch['sentence']=sentence
        batch['instance_label']=instance_label
        batch['image_id']=str(image_id)

        return batch

    def __load_data(self):
        start_t = time.time()
        self.lang=self._tranform_des()

        self.scene_list=sorted(list(set([data["scene_id"] for data in self.scanrefer])))
        if self.debug:
            self.scene_list=[self.scene_list[0]]

        self.scene_data={}
        for scene_id in self.scene_list:
            print("loading",scene_id)
            self.scene_data[scene_id]={}
            self.scene_data[scene_id]['bbox']=np.load(os.path.join(CONF.PATH.BBOX_PATH,scene_id,scene_id+"_bbox.npy"))
            ins2sem_path = os.path.join(CONF.PATH.INS2SEM_PATH, scene_id, scene_id + "_ins2sem.pkl")
            with open(ins2sem_path, 'rb') as f:
                ins2sem = p.load(f)
                self.scene_data[scene_id]["ins2sem"] = ins2sem

        # prepare class mapping
        lines = [line.rstrip() for line in open(CONF.PATH.SCANNET_V2_TSV)]
        lines = lines[1:]
        raw2nyuid = {}
        for i in range(len(lines)):
            elements = lines[i].split('\t')
            raw_name = elements[1]
            nyu40_name = int(elements[4])
            raw2nyuid[raw_name] = nyu40_name

        # store
        self.raw2nyuid = raw2nyuid
        self.raw2label = self._get_raw2label()
        end_t = time.time()
        print("it takes %f seconds to load the whole dataset"%(end_t-start_t))
    def _tranform_des(self):
        with open(CONF.PATH.GLOVE_PATH, "rb") as f:
            glove = pickle.load(f)

        lang = {}
        for data in self.scanrefer:
            scene_id = data["scene_id"]
            object_id = data["object_id"]
            ann_id = data["ann_id"]

            if scene_id not in lang:
                lang[scene_id] = {}

            if object_id not in lang[scene_id]:
                lang[scene_id][object_id] = {}

            if ann_id not in lang[scene_id][object_id]:
                lang[scene_id][object_id][ann_id] = {}

            # tokenize the description
            tokens = data["token"]
            embeddings = np.zeros((CONF.TRAIN.MAX_DES_LEN, 300))
            for token_id in range(CONF.TRAIN.MAX_DES_LEN):
                if token_id < len(tokens):
                    token = tokens[token_id]
                    if token in glove:
                        embeddings[token_id] = glove[token]
                    else:
                        embeddings[token_id] = glove["unk"]

            # store
            lang[scene_id][object_id][ann_id] = embeddings

        return lang

    def _get_raw2label(self):
        # mapping
        scannet_labels = {'cabinet': 0, 'bed': 1, 'chair': 2, 'sofa': 3, 'table': 4, 'door': 5,
                           'window': 6, 'bookshelf': 7, 'picture': 8, 'counter': 9, 'desk': 10, 'curtain': 11,
                           'refrigerator': 12, 'shower curtain': 13, 'toilet': 14, 'sink': 15, 'bathtub': 16,
                           'others': 17}.keys()
        scannet2label = {label: i for i, label in enumerate(scannet_labels)}

        lines = [line.rstrip() for line in open(CONF.PATH.SCANNET_V2_TSV)]
        lines = lines[1:]
        raw2label = {}
        for i in range(len(lines)):
            label_classes_set = set(scannet_labels)
            elements = lines[i].split('\t')
            raw_name = elements[1]
            nyu40_name = elements[7]
            if nyu40_name not in label_classes_set:
                raw2label[raw_name] = scannet2label['others']
            else:
                raw2label[raw_name] = scannet2label[nyu40_name]

        return raw2label

    def _translate(self, point_set, bbox,coor_on_img):
        # unpack
        coords = point_set[:, :3]

        # translation factors
        x_factor = np.random.choice(np.arange(-0.5, 0.501, 0.001), size=1)[0]
        y_factor = np.random.choice(np.arange(-0.5, 0.501, 0.001), size=1)[0]
        z_factor = np.random.choice(np.arange(-0.5, 0.501, 0.001), size=1)[0]
        factor = [x_factor, y_factor, z_factor]

        # dump
        coords += factor
        point_set[:, :3] = coords
        bbox[:, :3] += factor
        if coor_on_img is not None:
            coor_on_img[:,:,0:3]+=factor
        return point_set, bbox,coor_on_img


if __name__=="__main__":
    dataset=singleRGBD_dataset(isTrain=True,
                             num_points=20000,
                             use_color=True,
                             use_aug=True,
                             debug=True)
    for i in range(10):
        data_batch=dataset.__getitem__(i)