class RoundRobin:
    def __init__(self):
        self.pos = 0

    def schedule(self, client_list, params):
        total = len(client_list)
        select_num = int(params["c_ratio"] * len(client_list))
        if select_num < params["schedule_interval"] + 1:
            select_num = params["schedule_interval"] + 1

        print("Current clients:", total, ", select:", select_num)
        if self.pos + select_num <= total:
            selected_client_threads = client_list[self.pos:self.pos + select_num]
        else:
            selected_client_threads = client_list[self.pos:] + client_list[:select_num + self.pos - total]
        self.pos = (self.pos + select_num) % total
        return selected_client_threads
