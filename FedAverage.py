from modelUtil import *
from datasets_pfl import *
from FedUser import CDPUser, LDPUser, opacus
from FedServer import LDPServer, CDPServer
from datetime import date
import argparse
import time

# mine
from transformers import RobertaTokenizer
from datasets import load_dataset, concatenate_datasets
from torch.utils.data import DataLoader, random_split


start_time = time.time()

# Custom dataset to handle embedding data
class EmbeddingDataset(Dataset):
    def __init__(self, embeddings, labels):
        self.embeddings = embeddings
        self.labels = labels

    def __len__(self):
        return len(self.embeddings)

    def __getitem__(self, idx):
        return self.embeddings[idx], self.labels[idx]

# Function to load embeddings and labels from .npy files
def load_data(embeddings_path, labels_path):
    embeddings = np.load(embeddings_path)
    labels = np.load(labels_path)
    return embeddings, labels

def parse_arguments():
    parser = argparse.ArgumentParser()
    # mine
    parser.add_argument('--data', type=str, default='mnist',
                        choices=['mnist','cifar10','cifar100','fashionmnist','emnist','purchase','chmnist', 'imdb'])
    parser.add_argument('--nclient', type=int, default= 100)
    parser.add_argument('--nclass', type=int, help= 'the number of class for this dataset', default= 10)
    parser.add_argument('--ncpc', type=int, help= 'the number of class assigned to each client', default=2)
    # mine
    parser.add_argument('--model', type=str, default='mnist_fully_connected_IN', choices = ['mnist_fully_connected_IN', 'resnet18_IN', 'alexnet_IN', 'purchase_fully_connected_IN', 'mnist_fully_connected', 'resnet18', 'alexnet', 'purchase_fully_connected', 'SentimentClassifier', 'SentimentClassifier_IN'])
    parser.add_argument('--mode', type=str, default= 'LDP')
    parser.add_argument('--round',  type = int, default= 150)
    parser.add_argument('--epsilon', type=int, default=8)
    parser.add_argument('--physical_bs', type = int, default=3, help= 'the max_physical_batch_size of Opacus LDP, decrease if cuda out of memory')
    parser.add_argument('--sr',  type=float, default=1.0,
                        help='sample rate in each round')
    parser.add_argument('--lr',  type=float, default=1e-1,
                        help='learning rate')
    parser.add_argument('--flr',  type=float, default=1e-1,
                        help='learning rate')
    parser.add_argument('--E',  type=int, default=1,
                        help='the index of experiment in AE')
    parser.add_argument('--pretrained_head', type=str, default=None, help='path to pretrained classification head')
    args = parser.parse_args()
    return args

args = parse_arguments()

device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
today = date.today().isoformat()
DATA_NAME = args.data
NUM_CLIENTS = args.nclient
NUM_CLASSES = args.nclass
NUM_CLASES_PER_CLIENT= args.ncpc
MODEL = args.model
MODE = args.mode
EPOCHS = 1
ROUNDS = args.round
BATCH_SIZE = 64
LEARNING_RATE_DIS = args.lr
LEARNING_RATE_F = args.flr
mp_bs = args.physical_bs
target_epsilon = args.epsilon
target_delta = 1e-3
sample_rate=args.sr

os.makedirs(f'log/E{args.E}', exist_ok=True)
user_param = {'disc_lr': LEARNING_RATE_DIS, 'epochs': EPOCHS}
server_param = {}
if MODE == "LDP":
    user_obj = LDPUser
    server_obj = LDPServer
    user_param['rounds'] = ROUNDS
    user_param['target_epsilon'] = target_epsilon
    user_param['target_delta'] = target_delta
    user_param['sr'] = sample_rate
    user_param['mp_bs'] = mp_bs
elif MODE == "CDP":
    user_obj = CDPUser
    server_obj = CDPServer
    user_param['flr'] = LEARNING_RATE_F
    server_param['noise_multiplier'] = opacus.accountants.utils.get_noise_multiplier(target_epsilon=target_epsilon,
                                                                                 target_delta=target_delta, 
                                                                                 sample_rate=sample_rate, steps=ROUNDS)
    print(f"noise_multipier: {server_param['noise_multiplier']}")
    server_param['sample_clients'] = sample_rate*NUM_CLIENTS
else:
    raise ValueError("Choose mode from [CDP, LDP]")

if DATA_NAME == 'purchase':
    root = 'data/purchase/dataset_purchase'
elif DATA_NAME == 'chmnist':
    root = 'data/CHMNIST'
# mine
elif DATA_NAME == 'imdb':
    data_dir = 'data/imdb/'
    train_embeddings_path = os.path.join(data_dir, 'train_embeddings.npy')
    train_labels_path = os.path.join(data_dir, 'train_labels.npy')
    test_embeddings_path = os.path.join(data_dir, 'test_embeddings.npy')
    test_labels_path = os.path.join(data_dir, 'test_labels.npy')
    
    # Load embeddings and labels
    print("Loading precomputed embeddings and labels...")
    train_embeddings, train_labels = load_data(train_embeddings_path, train_labels_path)
    test_embeddings, test_labels = load_data(test_embeddings_path, test_labels_path)

    input_shape = train_embeddings.shape[1]

    # Create datasets
    train_dataset = EmbeddingDataset(train_embeddings, train_labels)
    test_dataset = EmbeddingDataset(test_embeddings, test_labels)

    # Create data loaders
    train_dataloaders = [DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True) for _ in range(NUM_CLIENTS)]
    test_dataloaders = [DataLoader(test_dataset, batch_size=BATCH_SIZE, shuffle=False) for _ in range(NUM_CLIENTS)]

else:
    root = '~/torch_data'
    train_dataloaders, test_dataloaders = gen_random_loaders(DATA_NAME, root, NUM_CLIENTS,
            BATCH_SIZE, NUM_CLASES_PER_CLIENT, NUM_CLASSES)

print(user_param)
users = [user_obj(i, device, MODEL, input_shape, NUM_CLASSES, train_dataloaders[i], **user_param) for i in range(NUM_CLIENTS)]
server = server_obj(device, MODEL, input_shape, NUM_CLASSES, **server_param)
for i in range(NUM_CLIENTS):
    users[i].set_model_state_dict(server.get_model_state_dict())
best_acc = 0
for round in range(ROUNDS):
    random_index = np.random.choice(NUM_CLIENTS, int(sample_rate*NUM_CLIENTS), replace=False)
    for index in random_index:users[index].train()
    if MODE == "LDP":
        weights_agg = agg_weights([users[index].get_model_state_dict() for index in random_index])
        for i in range(NUM_CLIENTS):
            users[i].set_model_state_dict(weights_agg)
    else:
        server.agg_updates([users[index].get_model_state_dict() for index in random_index])
        for i in range(NUM_CLIENTS):
            users[i].set_model_state_dict(server.get_model_state_dict())
    print(f"Round: {round+1}")
    acc = evaluate_global(users, test_dataloaders, range(NUM_CLIENTS))
    if acc > best_acc:
        best_acc = acc
    if MODE == "LDP":
        eps = max([user.epsilon for user in users])
        print(f"Epsilon: {eps}")
        if eps > target_epsilon:
            break

end_time = time.time()
print("Use time: {:.2f}h".format((end_time - start_time)/3600.0))
print(f'Best accuracy: {best_acc}')
results_df = pd.DataFrame(columns=["data","num_client","ncpc","mode","model","epsilon","accuracy"])
results_df = results_df._append(
    {"data": DATA_NAME, "num_client": NUM_CLIENTS,
     "ncpc": NUM_CLASES_PER_CLIENT, "mode":MODE,
     "model": MODEL, "epsilon": target_epsilon, "accuracy": best_acc},
    ignore_index=True)
results_df.to_csv(f'log/E{args.E}/{DATA_NAME}_{NUM_CLIENTS}_{NUM_CLASES_PER_CLIENT}_{MODE}_{MODEL}_{target_epsilon}.csv', index=False)


