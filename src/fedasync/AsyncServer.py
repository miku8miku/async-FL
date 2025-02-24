import threading

import torch.cuda

from fedasync import AsyncClientManager
from fedasync import SchedulerThread
from fedasync import UpdaterThread
from utils import ModuleFindTool, Queue, Time


class AsyncServer:
    def __init__(self, config, global_config, server_config, client_config, manager_config):
        self.config = config
        # 全局存储变量
        self.global_var = {'server': self}
        # 全局模型
        model_class = ModuleFindTool.find_class_by_path(server_config["model"]["path"])
        self.server_network = model_class(**server_config["model"]["params"])
        self.dev = 'cuda' if torch.cuda.is_available() else 'cpu'
        self.server_network = self.server_network.to(self.dev)

        # 数据集
        dataset_class = ModuleFindTool.find_class_by_path(global_config["dataset_path"])
        self.dataset = dataset_class(global_config["client_num"], global_config["iid"])
        self.test_data = self.dataset.get_test_dataset()
        self.config['global']['iid'] = self.dataset.get_config()
        self.T = server_config["epochs"]

        # 运行时变量
        self.current_t = Time.Time(1)
        self.schedule_t = Time.Time(1)
        self.queue = Queue.Queue()
        self.accuracy_list = []
        self.loss_list = []
        self.stop_event = threading.Event()
        self.stop_event.clear()
        self.server_thread_lock = threading.Lock()

        init_weights = self.server_network.state_dict()
        datasets = self.dataset.get_train_dataset()

        self.async_client_manager = AsyncClientManager.AsyncClientManager(init_weights, global_config["client_num"],
                                                                          global_config["multi_gpu"],
                                                                          datasets, self.queue, self.current_t,
                                                                          self.schedule_t,
                                                                          self.stop_event, client_config,
                                                                          manager_config, self.global_var)
        self.global_var['client_manager'] = self.async_client_manager
        self.scheduler_thread = SchedulerThread.SchedulerThread(self.server_thread_lock, self.async_client_manager,
                                                                self.queue, self.current_t, self.schedule_t,
                                                                server_config["scheduler"],
                                                                self.server_network, self.T, self.global_var)
        self.global_var['scheduler'] = self.scheduler_thread
        self.updater_thread = UpdaterThread.UpdaterThread(self.queue, self.server_thread_lock,
                                                          self.T, self.current_t, self.schedule_t, self.server_network,
                                                          self.stop_event, self.test_data, server_config["updater"],
                                                          server_config["scheduler"]["receiver"], self.global_var)
        self.global_var['updater'] = self.updater_thread

    def run(self):
        print("Start server:")

        # 启动server中的两个线程
        self.scheduler_thread.start()
        self.updater_thread.start()

        client_thread_list = self.async_client_manager.get_client_thread_list()
        for client_thread in client_thread_list:
            client_thread.join()
        self.scheduler_thread.join()
        print("scheduler_thread joined")
        self.updater_thread.join()
        print("updater_thread joined")
        print("Thread count =", threading.active_count())
        print(*threading.enumerate(), sep="\n")

        if not self.queue.empty():
            print("\nUn-used client weights:", self.queue.qsize())
            for q in range(self.queue.qsize()):
                self.queue.get()
        self.queue.close()

        self.accuracy_list, self.loss_list = self.updater_thread.get_accuracy_and_loss_list()
        del self.scheduler_thread
        del self.updater_thread
        del self.async_client_manager
        print("End!")

    def get_accuracy_and_loss_list(self):
        return self.accuracy_list, self.loss_list

    def get_config(self):
        return self.config
