import cv2
import os
import random
import numpy as np
import json
import pickle
import sys
from tqdm import tqdm

root = '.'
sys.path.insert(0, root)

from obman_render import (conditions, depthutils, imageutils, blender_scene)
from obman_render.grasps.grasputils import get_inv_hand_pca, grasp_wrong

class GraspRenderer:

    def __init__(self, results_root, backgrounds_path, obj_texture_path,
                 renderings_per_grasp=1, min_obj_ratio=0.4, render_body=False,
                 ambiant_mean=0.7, ambiant_add=0.5, z_min=0.3, z_max = 0.5, split="train"):
        assert split in ["train", "val", "test"], "split parameter has to be either train, val, or test!"
        assert 0.0 <= min_obj_ratio and min_obj_ratio <= 1.0, "min_obj_ratio has to be in [0, 1]!"

        self.min_obj_ratio = min_obj_ratio
        self.render_body = render_body
        self.frames_per_grasp = renderings_per_grasp
        self.z_min = z_min
        self.z_max = z_max
        self.split = split
        self.scene = blender_scene.BlenderScene(render_body,
                                                ambiant_mean=ambiant_mean,
                                                ambiant_add=ambiant_add)
        self._createResultDirectories(os.path.join(results_root, split))

        #print("filter_angle=" + str(94))
        print("renderings_per_grasp=" + str(self.frames_per_grasp))

        self.loadBackgrounds(backgrounds_path, self.split)
        self.loadBodyTextures(self.split)
        self.loadObjectTextures(obj_texture_path)


    def createConcatSegm(self, tmp_segm_path, tmp_segm_obj_path, tmp_segm_hand_path, frame_prefix):
        segm_img = cv2.imread(tmp_segm_path)[:, :, 0]
        obj_segm = cv2.imread(tmp_segm_obj_path)[:, :, 0]
        hand_segm = cv2.imread(tmp_segm_hand_path)[:, :, 0]
        keep_render_segm = conditions.segm_condition(segm_img, side='right', use_grasps=True)

        # Concatenate segm as rgb
        keep_render_obj, obj_ratio = conditions.segm_obj_condition(
            segm_img, obj_segm, min_obj_ratio=self.min_obj_ratio)
        keep_render = (self.render_body or keep_render_segm) and keep_render_obj

        if keep_render:
            segm_img = np.stack([segm_img, hand_segm, obj_segm], axis=2)
            # Write segmentation path
            segm_save_path = os.path.join(self.folder_segm,
                                          '{}.png'.format(frame_prefix))
            cv2.imwrite(segm_save_path, segm_img)

        return (keep_render, obj_ratio)


    def _createResultDirectories(self, results_root):
        # Set results folders
        self.folder_meta = os.path.join(results_root, 'meta')
        self.folder_rgb = os.path.join(results_root, 'rgb')
        self.folder_segm = os.path.join(results_root, 'segm')
        self.folder_temp_segm = os.path.join(results_root, 'tmp_segm')
        self.folder_depth = os.path.join(results_root, 'depth')
        self.folder_rgb_hand = os.path.join(results_root, 'rgb_hand')
        self.folder_rgb_obj = os.path.join(results_root, 'rgb_obj')
        self.folder_depth_hand = os.path.join(results_root, 'depth_hand')
        self.folder_depth_obj = os.path.join(results_root, 'depth_obj')
        folders = [
            self.folder_meta,
            self.folder_rgb,
            self.folder_segm,
            self.folder_temp_segm,
            self.folder_depth,
            self.folder_rgb_hand,
            self.folder_rgb_obj
        ]
        # Create results directories
        for folder in folders:
            os.makedirs(folder, exist_ok=True)


    def _createSplit(self, path, fraction_train=0.8, fraction_valid=0.0):
        assert fraction_train + fraction_valid < 1.0, 'fraction_train + fraction_valid = {} > 1.0 !'.format(fraction_train + fraction_valid)
        files = [os.path.join(path, f) for f in os.listdir(path)]
        count = len(files)
        np.random.shuffle(files)

        split_idx_1 = int(fraction_train * count)
        split_idx_2 = int(fraction_valid * count) + split_idx_1
        train_split = files[:split_idx_1]
        valid_split = files[split_idx_1:split_idx_2]
        test_split = files[split_idx_2:]
        
        split_path = os.path.join(path, "split.json")
        with open(split_path, 'w') as split_f:
            json.dump({'train': train_split, 'val': valid_split, 'test': test_split}, split_f)


    def loadBackgrounds(self, path, split="train"):
        #TODO prepare for RGB-D (e.g. copy split file from rgb to depth path?)
        split_path = os.path.join(path, "split.json")
        if not os.path.exists(split_path):
            self._createSplit(path)

        with open(split_path, "r") as split_f:
            raw_json = json.load(split_f)
        self.backgrounds = raw_json[split]
        print('Got {} backgrounds'.format(len(self.backgrounds)))


    def loadBodyTextures(self, split="train"):
        self.body_textures = imageutils.get_bodytexture_paths(["bodywithands"], split=split)
        print('Got {} body textures'.format(len(self.body_textures)))


    def loadGraspFiles(self, path, split="train"):
        pattern = ".{}.json".format(split)
        grasp_files = os.listdir(path)
        grasp_files = [os.path.join(path, grasp_file) for grasp_file in grasp_files if pattern in grasp_file]
        return grasp_files


    def loadObjectTextures(self, path):
        #TODO is a split parameter necessary?
        self.obj_textures = [os.path.join(path, f) for f in os.listdir(path)]
        print('Got {} object textures'.format(len(self.obj_textures)))


    def renderDepth(self, tmp_depth, tmp_hand_depth, tmp_obj_depth, frame_prefix):
        depth, depth_min, depth_max = depthutils.convert_depth(tmp_depth)

        # Concatenate depth as rgb
        hand_depth, hand_depth_min, hand_depth_max = depthutils.convert_depth(
            tmp_hand_depth)
        obj_depth, obj_depth_min, obj_depth_max = depthutils.convert_depth(
            tmp_obj_depth)

        # Write depth image
        depth = np.stack([depth, hand_depth, obj_depth], axis=2)
        final_depth_path = os.path.join(self.folder_depth,
                                        '{}.png'.format(frame_prefix))
        cv2.imwrite(final_depth_path, depth)

        depth_info = {
            'depth_min': depth_min,
            'depth_max': depth_max,
            'hand_depth_min': hand_depth_min,
            'hand_depth_max': hand_depth_max,
            'obj_depth_min': obj_depth_min,
            'obj_depth_max': obj_depth_max
        }
        return depth_info


    def renderGrasp(self, grasp, grasp_idx):
        assert self.backgrounds is not None, "No backgrounds loaded!"
        assert self.body_textures is not None, "No body textures loaded!"

        obj_path = grasp['obj_path']
        model_name = os.path.basename(obj_path)

        # Get list of already existing frames
        rendered_frames = os.listdir(self.folder_meta)

        frame_idx = 0
        while (frame_idx < self.frames_per_grasp):
            frame_prefix = "{}_grasp{:03d}_{:04d}".format(model_name, grasp_idx + 1, frame_idx + 1)

            # Check if frame has already been rendered
            if "{}.pkl".format(frame_prefix) in rendered_frames:
                print("Found rendered frame {}, continuing.".format(frame_prefix))
                frame_idx += 1
                continue
            else:
                print("\nWorking on {}".format(frame_prefix))

            # Load object
            obj_info = self.scene.loadObject(obj_path)
            #obj_texture_info, obj_osl_path, obj_oso_path = self.scene.addObjectTexture(obj_path,
            #                                                                           obj_textures=self.obj_textures,
            #                                                                           random_obj_textures=False)
            self.scene.setToolMaterialPassIndices()
            # Keep track of temporary files to delete at the end
            tmp_files = []
            #tmp_files.append(obj_osl_path)
            #tmp_files.append(obj_oso_path)

            # Keep track of meta data
            meta_infos = {}
            meta_infos.update(obj_info)
            #meta_infos.update(obj_texture_info)

            # Set hand and object pose
            hand_info = self.scene.setHandAndObjectPose(grasp, self.z_min, self.z_max)
            meta_infos.update(hand_info)

            # Save grasp info
            for label in [
                'obj_path', 'pca_pose', 'grasp_quality',
                'grasp_epsilon', 'grasp_volume', 'hand_trans',
                'hand_global_rot', 'hand_pose'
            ]:
                meta_infos[label] = grasp[label]

            # Randomly pick background
            bg_path = random.choice(self.backgrounds)
            meta_infos['bg_path'] = bg_path

            # Randomly pick clothing texture
            tex_path = random.choice(self.body_textures)
            meta_infos['body_tex'] = tex_path
            self.scene.setSMPLTexture(tex_path)
            self.scene.setHandTextures()

            # Set lighting conditions
            lighting_info = self.scene.setLighting()
            meta_infos.update(lighting_info)

            # Render RGB
            img_path = os.path.join(self.folder_rgb, '{}.jpg'.format(frame_prefix))
            depth_path = os.path.join(self.folder_depth, frame_prefix)
            tmp_depth = depth_path + '{:04d}.exr'.format(1)
            tmp_segm_path = self.scene.renderRGB(img_path, bg_path, depth_path, self.folder_temp_segm)
            tmp_files.append(tmp_segm_path)
            tmp_files.append(tmp_depth)

            # Render RGB obj only
            obj_img_path = os.path.join(self.folder_rgb_obj,
                                        '{}.jpg'.format(frame_prefix))
            obj_depth_path = os.path.join(self.folder_depth_obj, frame_prefix)
            tmp_obj_depth = obj_depth_path + '{:04d}.exr'.format(1)
            tmp_segm_obj_path = self.scene.renderRGB(obj_img_path, bg_path, obj_depth_path,
                                                     self.folder_temp_segm, hide_smplh=True)
            tmp_files.append(tmp_segm_obj_path)
            tmp_files.append(tmp_obj_depth)

            # Render RGB hand only
            hand_img_path = os.path.join(self.folder_rgb_hand,
                                         '{}.jpg'.format(frame_prefix))
            hand_depth_path = os.path.join(self.folder_depth_hand, frame_prefix)
            tmp_hand_depth = hand_depth_path + '{:04d}.exr'.format(1)
            tmp_segm_hand_path = self.scene.renderRGB(hand_img_path, bg_path, hand_depth_path,
                                                     self.folder_temp_segm, hide_obj=True)
            tmp_files.append(tmp_segm_hand_path)
            tmp_files.append(tmp_hand_depth)

            # Check camera pose again (not sure why?)
            self.scene.checkCamera()

            keep_render, obj_ratio = self.createConcatSegm(tmp_segm_path, tmp_segm_obj_path,
                                                           tmp_segm_hand_path, frame_prefix)
            meta_infos['obj_visibility_ratio'] = obj_ratio

            if keep_render:
                # Render depth image
                depth_infos = self.renderDepth(tmp_depth, tmp_hand_depth, tmp_obj_depth, frame_prefix)
                meta_infos.update(depth_infos)

                # Save meta
                meta_path = os.path.join(self.folder_meta,
                                             '{}.pkl'.format(frame_prefix))

                with open(meta_path, 'wb') as meta_f:
                    pickle.dump(meta_infos, meta_f)

                frame_idx += 1
            else:
                print("Discarding rendered image. frame_idx: {:04d}".format(frame_idx + 1))
                tmp_files.append(img_path)
                tmp_files.append(obj_img_path)
                tmp_files.append(hand_img_path)

            # Remove temporary files
            for filepath in tmp_files:
                if os.path.isfile(filepath):
                    os.remove(filepath)

            # Delete object
            self.scene.clearUnused()
            self.scene.deleteObject()
            self.scene.deleteMaterials()


    def renderGraspsInDir(self, grasp_folder, mano_right_path, smpl_model_path, smpl_data_path,
                          texture_zoom=1, max_grasps_per_object=2, filter_angle=94):
        # Load grasp files
        grasp_files = self.loadGraspFiles(grasp_folder, self.split)
        print("Found {} json grasp files.".format(len(grasp_files)))

        # Load hand model
        self.scene.loadSMPLHModel(mano_right_path, smpl_model_path, smpl_data_path,
                                  texture_zoom=texture_zoom)

        inv_hand_pca = get_inv_hand_pca(mano_path=mano_right_path)

        for grasp_file in grasp_files:
            with open(grasp_file, 'r') as f:
                grasp_list = json.load(f)
            grasp_count = len(grasp_list)
            print("Found {} grasps for object {}".format(grasp_count, grasp_file))

            grasps_rendered = 0
            for idx, grasp in enumerate(grasp_list):
                if grasps_rendered >= max_grasps_per_object:
                    print("Reached maximum of {} grasps for object {}".format(max_grasps_per_object, grasp_file))
                    break

                if grasp_wrong(grasp, angle=filter_angle):
                    print("Skipping wrong grasp.")
                    continue

                grasp_info = {
                    'obj_path':
                        grasp['body'],
                    'sample_scale':
                        1.0,
                    'pose':
                        grasp['pose'],
                    'hand_pose':
                        grasp['mano_pose'],
                    'hand_trans':
                        grasp['mano_trans'][0],
                    'pca_pose':
                        np.array(
                            grasp['mano_pose'][3:]).dot(inv_hand_pca),
                    'hand_global_rot':
                        grasp['mano_pose'][:3],
                    'grasp_quality':
                        grasp['quality'],
                    'grasp_epsilon':
                        grasp['epsilon'],
                    'grasp_volume':
                        grasp['volume']
                }

                self.renderGrasp(grasp_info, idx)
                grasps_rendered += 1


if __name__ == "__main__":
    root = '.'
    sys.path.insert(0, root)
    recover_json_string = ' '.join(sys.argv[sys.argv.index('--') + 1:])

    config = {
        "results_root": "datageneration/tmp/",
        "grasp_folder": "assets/grasps/",
        "max_grasps_per_object": 2,
        "mano_right_path": "assets/models/MANO_RIGHT.pkl",
        "smpl_model_path": "assets/models/SMPLH_female.pkl",
        "smpl_data_path": "assets/SURREAL/smpl_data/smpl_data.npz",
        "obj_texture_path": "assets/model_textures/",
        "backgrounds_path": "assets/backgrounds/",
        "ambiant_mean": 0.7,
        "ambiant_add": 0.5,
        "renderings_per_grasp": 1,
        "min_obj_ratio": 0.4,
        "texture_zoom": 1,
        "render_body": True,
        "split": "train"
    }

    json_config = json.loads(recover_json_string)
    config.update(json_config)

    gr = GraspRenderer(results_root=config["results_root"],
                       obj_texture_path=config["obj_texture_path"],
                       backgrounds_path=config["backgrounds_path"],
                       renderings_per_grasp=config["renderings_per_grasp"],
                       ambiant_add=config["ambiant_add"],
                       ambiant_mean=config["ambiant_mean"],
                       min_obj_ratio=config["min_obj_ratio"],
                       render_body=config["render_body"],
                       split=config["split"])
    gr.renderGraspsInDir(grasp_folder=config["grasp_folder"],
                         mano_right_path=config["mano_right_path"],
                         smpl_model_path=config["smpl_model_path"],
                         smpl_data_path=config["smpl_data_path"],
                         texture_zoom=config["texture_zoom"],
                         max_grasps_per_object=config["max_grasps_per_object"])

    print("Dataset creation complete!")
    exit(0)