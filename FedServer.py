from modelUtil import *
from collections import OrderedDict
import opacus
from opacus.validators import ModuleValidator


class CDPServer:
    def __init__(self, device, model, input_shape, n_classes, noise_multiplier=1, sample_clients=10, disc_lr=1, pretrained_head=None):
        # mine
        if model == 'SentimentClassifier':
            self.model = SentimentClassifier(input_shape)
            if pretrained_head:
                self.model.load_pretrained_head(pretrained_head)
        elif model == 'SentimentClassifier_IN':
            self.model = SentimentClassifier_IN(input_shape)
            if pretrained_head:
                self.model.load_pretrained_head(pretrained_head)

        elif 'linear_model' in model:
            self.model = globals()[model](num_classes=n_classes, input_shape=input_shape)
        else:
            self.model = globals()[model](num_classes=n_classes)
        self.disc_lr = disc_lr
        self.device = device
        self.sample_clients = sample_clients
        self.noise_multiplier = noise_multiplier
        self.trainable_names = [k for k, _ in self.model.named_parameters()]
        self.agg = True
        if "IN" in model:
            self.agg = False

    def get_median_norm(self, weights):
        median_norm = OrderedDict()
        for k, v in self.model.named_parameters():
            norms = []
            for i in range(len(weights)):
                grad =  v.detach()-weights[i][k]
                norms.append(grad.norm(2))
            median_norm[k] = min(median(norms), 10)
        # print(median_norm)
        return median_norm

    def get_model_state_dict(self):
        return self.model.state_dict()

    def agg_updates(self, weights):
        with torch.no_grad():
            norms = self.get_median_norm(weights)
            if self.agg == False:
                for k, v in self.get_model_state_dict().items():
                    if 'bn' not in k and 'norm' not in k and 'downsample.1' not in k:
                        sumed_grad = torch.zeros_like(v)
                        for i in range(len(weights)):
                            grad = weights[i][k]-v
                            grad = grad*min(1, norms[k]/grad.norm(2))
                            sumed_grad += grad
                        sigma = norms[k]*self.noise_multiplier
                        sumed_grad += torch.normal(0, sigma, v.shape)
                        value = v + sumed_grad/self.sample_clients
                        self.model.state_dict()[k].data.copy_(value.detach().clone())
            else:
                for k, v in self.get_model_state_dict().items():
                    if 'bn' not in k:
                        sumed_grad = torch.zeros_like(v)
                        for i in range(len(weights)):
                            grad = weights[i][k]-v
                            grad = grad*min(1, norms[k]/grad.norm(2))
                            sumed_grad += grad
                        sigma = norms[k]*self.noise_multiplier
                        sumed_grad += torch.normal(0, sigma, v.shape)
                        value = v + sumed_grad/self.sample_clients
                        self.model.state_dict()[k].data.copy_(value.detach().clone())


class LDPServer(CDPServer):
    def __init__(self, device, model, n_classes, input_shape, noise_multiplier=1, sample_clients=10, disc_lr=1, pretrained_head=None):
        super().__init__(device, model, n_classes, input_shape, noise_multiplier, sample_clients, disc_lr, pretrained_head=pretrained_head)
        self.model = ModuleValidator.fix(self.model)
        self.privacy_engine = opacus.PrivacyEngine()
        self.model = self.privacy_engine._prepare_model(self.model)
        self.agg = True
        if "IN" in model:
            self.agg = False

    def agg_updates(self, weights):
        with torch.no_grad():
            if self.agg == False:
                for k, v in self.get_model_state_dict().items():
                    if 'bn' not in k and 'norm' not in k and 'downsample.1' not in k:
                        sumed_grad = torch.zeros_like(v)
                        for i in range(len(weights)):
                            grad = weights[i][k]-v
                            sumed_grad += grad
                        value = v + sumed_grad/self.sample_clients
                        self.model.state_dict()[k].data.copy_(value.detach().clone())
            else:
                for k, v in self.get_model_state_dict().items():
                    if 'bn' not in k:
                        sumed_grad = torch.zeros_like(v)
                        for i in range(len(weights)):
                            grad = weights[i][k]-v
                            sumed_grad += grad
                        value = v + sumed_grad/self.sample_clients
                        self.model.state_dict()[k].data.copy_(value.detach().clone())

