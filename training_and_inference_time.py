import os.path
import os.path as osp
from math import ceil
import time
import sys
import torch
import torch.nn.functional as F
from tqdm import tqdm
import torch_geometric.transforms as T
from torch_geometric.datasets import TUDataset
from torch_geometric.loader import DenseDataLoader
from torch_geometric.nn import DenseSAGEConv, dense_diff_pool

max_nodes = 150


class MyFilter:
    def __call__(self, data):
        return data.num_nodes <= max_nodes


class GNN(torch.nn.Module):
    def __init__(self, in_channels, hidden_channels, out_channels,
                 normalize=False, lin=True):
        super().__init__()

        self.conv1 = DenseSAGEConv(in_channels, hidden_channels, normalize)
        self.bn1 = torch.nn.BatchNorm1d(hidden_channels)
        self.conv2 = DenseSAGEConv(hidden_channels, hidden_channels, normalize)
        self.bn2 = torch.nn.BatchNorm1d(hidden_channels)
        self.conv3 = DenseSAGEConv(hidden_channels, out_channels, normalize)
        self.bn3 = torch.nn.BatchNorm1d(out_channels)

        if lin is True:
            self.lin = torch.nn.Linear(2 * hidden_channels + out_channels,
                                       out_channels)
        else:
            self.lin = None

    def bn(self, i, x):
        batch_size, num_nodes, num_channels = x.size()

        x = x.view(-1, num_channels)
        x = getattr(self, f'bn{i}')(x)
        x = x.view(batch_size, num_nodes, num_channels)
        return x

    def forward(self, x, adj, mask=None):
        batch_size, num_nodes, in_channels = x.size()

        x0 = x
        if len(adj.size()) == 4:
            adj = adj[:, :, :, 0]
        x1 = self.bn(1, self.conv1(x0, adj, mask).relu())
        x2 = self.bn(2, self.conv2(x1, adj, mask).relu())
        x3 = self.bn(3, self.conv3(x2, adj, mask).relu())

        x = torch.cat([x1, x2, x3], dim=-1)

        if self.lin is not None:
            x = self.lin(x).relu()

        return x


class Net(torch.nn.Module):
    def __init__(self, dataset):
        super().__init__()

        num_nodes = ceil(0.25 * max_nodes)
        self.gnn1_pool = GNN(dataset.num_features, 64, num_nodes)
        self.gnn1_embed = GNN(dataset.num_features, 64, 64, lin=False)

        num_nodes = ceil(0.25 * num_nodes)
        self.gnn2_pool = GNN(3 * 64, 64, num_nodes)
        self.gnn2_embed = GNN(3 * 64, 64, 64, lin=False)

        self.gnn3_embed = GNN(3 * 64, 64, 64, lin=False)

        self.lin1 = torch.nn.Linear(3 * 64, 64)
        self.lin2 = torch.nn.Linear(64, dataset.num_classes)

    def forward(self, x, adj, mask=None):
        s = self.gnn1_pool(x, adj, mask)
        x = self.gnn1_embed(x, adj, mask)
        if len(adj.size()) == 4:
            adj = adj[:, :, :, 0]
        x, adj, l1, e1 = dense_diff_pool(x, adj, s, mask)

        s = self.gnn2_pool(x, adj)
        x = self.gnn2_embed(x, adj)

        x, adj, l2, e2 = dense_diff_pool(x, adj, s)

        x = self.gnn3_embed(x, adj)

        x = x.mean(dim=1)
        x = self.lin1(x).relu()
        x = self.lin2(x)
        return F.log_softmax(x, dim=-1), l1 + l2, e1 + e2


def train(epoch, train_loader, model, device, optimizer, length_train_dataset):
    model.train()
    loss_all = 0

    for data in train_loader:
        data = data.to(device)
        optimizer.zero_grad()
        output, _, _ = model(data.x, data.adj, data.mask)
        loss = F.nll_loss(output, data.y.view(-1))
        loss.backward()
        loss_all += data.y.size(0) * float(loss)
        optimizer.step()
    return loss_all / length_train_dataset


@torch.no_grad()
def test(loader, model, device):
    model.eval()
    correct = 0

    for data in loader:
        data = data.to(device)
        pred = model(data.x, data.adj, data.mask)[0].max(dim=1)[1]
        correct += int(pred.eq(data.y.view(-1)).sum())
    return correct / len(loader.dataset)


if __name__ == "__main__":
    args = sys.argv
    dataset_name = args[1]
    path = osp.join(osp.dirname(osp.realpath(__file__)), '..', 'data',
                    'mutag_dense')
    dataset = TUDataset(path, name=dataset_name, transform=T.ToDense(max_nodes),
                        pre_filter=MyFilter(), use_edge_attr=False)
    dataset = dataset.shuffle()
    n = (len(dataset) + 9) // 10
    test_dataset = dataset[:n]
    val_dataset = dataset[n:2 * n]
    train_dataset = dataset[2 * n:]
    base_path = "/home/kinaan/PycharmProjects/pytorch_playbook_dev/results_final/inference_time/diffpool/"
    if not os.path.exists(base_path):
        os.mkdir(base_path)

    if not os.path.exists(base_path + dataset_name):
        os.mkdir(base_path + dataset_name)

    for i in tqdm(range(10)):
        test_data_loader = DenseDataLoader(test_dataset, batch_size=1)
        val_data_loader = DenseDataLoader(val_dataset, batch_size=1)
        train_data_loader = DenseDataLoader(train_dataset, batch_size=1)
        device = "cpu"  # torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        model = Net(train_dataset).to(device)
        optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
        best_val_acc = test_acc = 0
        for epoch in range(1):
            start_time = time.time()
            train_loss = train(epoch, train_data_loader, model, device, optimizer, len(train_dataset))
            end_time = time.time() - start_time
            with open(base_path + dataset_name + "/training.txt", "a+") as f:
                f.write(str(end_time) + "\n")
            # val_acc = test(val_data_loader, model, device)
            # if val_acc > best_val_acc:
            start_time = time.time()
            test_acc = test(test_data_loader, model, device)
            end_time = time.time() - start_time

            with open(base_path + dataset_name + "/inference.txt", "a+") as f:
                f.write(str(end_time) + "\n")
