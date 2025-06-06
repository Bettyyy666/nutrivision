import os
import torch
import numpy as np
import cv2
from torchvision.models.detection import maskrcnn_resnet50_fpn
from torchvision.models.detection.faster_rcnn import FastRCNNPredictor
from torchvision.models.detection.mask_rcnn import MaskRCNNPredictor
import torchvision.transforms as T
from PIL import Image
from diffusers import StableDiffusionInpaintPipeline

# -------- CONFIG --------
MODEL_PATH = "mrcnn_foodseg103.pth"
NUM_CLASSES = 104  # 103 food classes + background
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
INPUT_IMAGE = "images/00004520.jpg"
OUTPUT_DIR = "output"
CATEGORY_PATH = "../FoodSeg103/category_id.txt"

# -------- MODEL SETUP --------
def get_model():
    model = maskrcnn_resnet50_fpn(weights=None)
    in_features = model.roi_heads.box_predictor.cls_score.in_features
    model.roi_heads.box_predictor = FastRCNNPredictor(in_features, NUM_CLASSES)

    in_features_mask = model.roi_heads.mask_predictor.conv5_mask.in_channels
    hidden_layer = 512
    model.roi_heads.mask_predictor = MaskRCNNPredictor(in_features_mask, hidden_layer, NUM_CLASSES)

    model.load_state_dict(torch.load(MODEL_PATH, map_location=DEVICE))
    model.to(DEVICE)
    model.eval()
    return model

# -------- LOAD CLASS NAMES --------
def load_class_names(category_file):
    class_names = []
    with open(category_file, 'r') as f:
        for line in f:
            _, name = line.strip().split(maxsplit=1)
            class_names.append(name)
    return class_names

CLASS_NAMES = load_class_names(CATEGORY_PATH)

# -------- DRAW PREDICTIONS --------
def draw_instance_predictions(img, boxes, masks, labels, scores, score_thresh=0.5):
    for i in range(len(masks)):
        if scores[i] < score_thresh:
            continue

        mask = masks[i]
        color = np.random.randint(0, 255, (3,), dtype=np.uint8)
        color_mask = np.stack([mask * color[j] for j in range(3)], axis=-1)
        img = np.where(mask[:, :, None], img * 0.5 + color_mask * 0.5, img)

        # Compute center of mass of mask to place the label
        coords = np.column_stack(np.where(mask))
        if coords.shape[0] == 0:
            continue  # Skip empty masks
        y_center, x_center = coords.mean(axis=0).astype(int)

        # Prepare label text
        label = CLASS_NAMES[labels[i]] if labels[i] < len(CLASS_NAMES) else str(labels[i])

        # Draw label inside the mask
        cv2.putText(
            img, label, (x_center, y_center),
            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), thickness=1, lineType=cv2.LINE_AA
        )

    return img.astype(np.uint8)

# -------- MAIN FUNCTION --------
def run_inference(image_path):
    model = get_model()

    # Load + resize image
    image = Image.open(image_path).convert("RGB").resize((256, 192))
    orig = np.array(image)

    # Save the resized input image
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    base_name = os.path.basename(image_path)
    name, _ = os.path.splitext(base_name)
    resized_input_path = os.path.join(OUTPUT_DIR, f"{name}_resized.jpg")
    cv2.imwrite(resized_input_path, cv2.cvtColor(orig, cv2.COLOR_RGB2BGR))
    print(f"[INFO] Saved resized input to {resized_input_path}")

    transform = T.ToTensor()
    img_tensor = transform(image).to(DEVICE)

    with torch.no_grad():
        output = model([img_tensor])[0]

    masks = output["masks"].squeeze(1).cpu().numpy() > 0.5
    boxes = output["boxes"].cpu().numpy()
    labels = output["labels"].cpu().numpy()
    scores = output["scores"].cpu().numpy()

    result = draw_instance_predictions(orig.copy(), boxes, masks, labels, scores)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    base_name = os.path.basename(image_path)
    name, _ = os.path.splitext(base_name)
    output_path = os.path.join(OUTPUT_DIR, f"{name}_masked.jpg")
    cv2.imwrite(output_path, cv2.cvtColor(result, cv2.COLOR_RGB2BGR))
    print(f"[INFO] Saved result image to {output_path}")

    # food subsitution
    merged_mask = np.any(masks, axis=0).astype(np.uint8)
    user_prompt = "scrambled egg"
    inpainted_output_path = os.path.join(OUTPUT_DIR, f"{name}_inpainted.jpg")
    run_inpainting(orig, merged_mask, user_prompt, inpainted_output_path)

# -------- INPAINTING FUNCTION --------
def run_inpainting(original_img, binary_mask, prompt, output_path="output/inpainted_result.png"):
    # Convert np array to PIL images
    image_pil = Image.fromarray(original_img.astype(np.uint8)).convert("RGB")
    mask_pil = Image.fromarray((binary_mask * 255).astype(np.uint8)).convert("L")

    # Load the inpainting model (assumes it's downloaded)
    pipe = StableDiffusionInpaintPipeline.from_pretrained(
        "Lykon/dreamshaper-8-inpainting", torch_dtype=torch.float16
    ).to(DEVICE)

    # Run the model
    result = pipe(prompt=prompt, image=image_pil, mask_image=mask_pil).images[0]
    result.save(output_path)
    print(f"[INFO] Inpainting result saved to {output_path}")

if __name__ == "__main__":
    run_inference(INPUT_IMAGE)