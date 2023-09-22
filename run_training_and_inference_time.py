import os

if __name__ == "__main__":
    datasets = ["MUTAG", "PTC_FM", "BZR", "PROTEINS", "ENZYMES", "COX", "NCI1", "NCI109"]

    for dataset in datasets:
        os.system("python training_and_inference_time.py " + dataset)
