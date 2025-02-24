import threading
import time

import torch.utils.data
import wandb
from torch.utils.data import DataLoader

from utils import ModuleFindTool


class UpdaterThread(threading.Thread):
    def __init__(self, queue, server_thread_lock, t, current_t, server_network,
                 stop_event, test_data, updater_config, mutex_sem, empty_sem, full_sem, global_var):
        threading.Thread.__init__(self)
        self.mutex_sem = mutex_sem
        self.empty_sem = empty_sem
        self.full_sem = full_sem
        self.queue = queue
        self.server_thread_lock = server_thread_lock
        self.T = t
        self.current_time = current_t
        self.server_network = server_network
        self.global_var = global_var
        self.sync_client_manager = global_var['client_manager']
        self.stop_event = stop_event
        self.test_data = test_data

        self.accuracy_list = []
        self.loss_list = []
        self.config = updater_config
        update_class = ModuleFindTool.find_class_by_path(updater_config["updater_path"])
        self.update = update_class(self.config["params"], self)

        # loss函数
        if isinstance(updater_config["loss"], str):
            self.loss_func = ModuleFindTool.find_class_by_path(f'torch.nn.functional.{updater_config["loss"]}')
        else:
            self.loss_func = ModuleFindTool.find_class_by_path(
                f'loss.{updater_config["loss"]["loss_file"]}.{updater_config["loss"]["loss_name"]}')

    def run(self):
        for epoch in range(self.T):
            self.full_sem.acquire()
            self.mutex_sem.acquire()
            update_list = []
            # 接收所有的更新
            while not self.queue.empty():
                update_list.append(self.queue.get())

            self.server_thread_lock.acquire()
            self.update_server_weights(epoch, update_list)
            self.run_server_test(epoch)
            self.server_thread_lock.release()
            time.sleep(0.01)

            # self.check_in_thread_lock.release()
            self.current_time.time_add()
            # 本轮结束
            self.mutex_sem.release()
            self.empty_sem.release()

        # 终止所有client线程
        self.sync_client_manager.stop_all_clients()

    def update_server_weights(self, epoch, update_list):
        if not isinstance(update_list, list):
            print("-------------- !!!error!!! --------------")
            print("using asynchronous update method")
        updated_parameters = self.update.update_server_weights(epoch, update_list)
        for key, var in updated_parameters.items():
            if torch.cuda.is_available():
                updated_parameters[key] = updated_parameters[key].cuda()
        self.server_network.load_state_dict(updated_parameters)

    def run_server_test(self, epoch):
        dl = DataLoader(self.test_data, batch_size=100, shuffle=True, drop_last=True)
        test_correct = 0
        test_loss = 0
        dev = 'cuda' if torch.cuda.is_available() else 'cpu'
        for data in dl:
            inputs, labels = data
            inputs, labels = inputs.to(dev), labels.to(dev)
            outputs = self.server_network(inputs)
            _, id = torch.max(outputs.data, 1)
            test_correct += torch.sum(id == labels.data).cpu().numpy()
            test_loss += self.loss_func(outputs, labels).item()
        accuracy = test_correct / len(dl)
        loss = test_loss / len(dl)
        self.accuracy_list.append(accuracy)
        self.loss_list.append(loss)
        print('Epoch(t):', epoch, 'accuracy:', accuracy, 'loss', loss)
        if self.config['enabled']:
            wandb.log({'accuracy': accuracy, 'loss': loss})
        return accuracy

    def get_accuracy_and_loss_list(self):
        return self.accuracy_list, self.loss_list
