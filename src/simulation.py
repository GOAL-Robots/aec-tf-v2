from pyrep import PyRep
from pyrep.objects import VisionSensor
import multiprocessing as mp
import os
from pathlib import Path
from collections import defaultdict
import numpy as np
from contextlib import contextmanager
from traceback import format_exc
import time


MODEL_PATH = os.environ["COPPELIASIM_MODEL_PATH"]


class SimulationConsumerFailed(Exception):
    def __init__(self, consumer_exception, consumer_traceback):
        self.consumer_exception = consumer_exception
        self.consumer_traceback = consumer_traceback

    def __str__(self):
        return '\n\nFROM CONSUMER:\n\n{}'.format(self.consumer_traceback)

def communicate_return_value(method):
    """method from the SimulationConsumer class decorated with this function
    will send there return value to the SimulationProducer class"""
    method._communicate_return_value = True
    return method


def default_dont_communicate_return(cls):
    """Class decorator for the SimulationConsumers meaning that by default, all
    methods don't communicate their return value to the Producer class"""
    for attribute_name, attribute in cls.__dict__.items():
        if callable(attribute):
            communicate = hasattr(attribute, '_communicate_return_value')
            attribute._communicate_return_value = communicate
    return cls


def c2p_convertion_function(cls, method):
    """Function that transform a Consumer method into a Producer method.
    It add a blocking flag that determines whether the call is blocking or not.
    If you call a `Producer.mothod(blocking=False)`, you then must
    `Producer._wait_for_answer()`"""
    def new_method(self, *args, blocking=True, **kwargs):
        cls._send_command(self, method, *args, **kwargs)
        if method._communicate_return_value and blocking:
            return cls._wait_for_answer(self)
    new_method._communicate_return_value = method._communicate_return_value
    return new_method


def consumer_to_producer_method_conversion(cls):
    """Class decorator that transforms all methods from the Consumer to the
    Producer, except for methods starting with an '_', and for the
    multiprocessing.Process methods"""
    proc_methods = [
        "run", "is_alive", "join", "kill", "start", "terminate", "close"
    ]
    method_dict = {
        **SimulationConsumerAbstract.__dict__,
        **SimulationConsumer.__dict__,
    }
    convertables = {
        method_name: method \
        for method_name, method in method_dict.items()\
        if callable(method) and\
        method_name not in proc_methods and\
        not method_name.startswith("_")
    }
    for method_name, method in convertables.items():
        new_method = c2p_convertion_function(cls, method)
        setattr(cls, method_name, new_method)
    return cls


def p2p_convertion_function(name):
    """This function transforms a producer method into a Pool method"""
    def new_method(self, *args, **kwargs):
        if self._distribute_args_mode:
            # all args are iterables that must be distributed to each producer
            for i, producer in enumerate(self._active_producers):
                getattr(producer, name)(
                    *[arg[i] for arg in args],
                    blocking=False,
                    **{key: value[i] for key, value in kwargs.items()}
                )
        else:
            for producer in self._active_producers:
                getattr(producer, name)(*args, blocking=False, **kwargs)
        if getattr(SimulationProducer, name)._communicate_return_value:
            return [
                producer._wait_for_answer() for producer in self._active_producers
            ]
    return new_method

def producer_to_pool_method_convertion(cls):
    """This class decorator transforms all Producer methods (besides close and
    methods starting with '_') to the Pool object."""
    convertables = {
        method_name: method \
        for method_name, method in SimulationProducer.__dict__.items()\
        if callable(method) and not method_name.startswith("_")\
        and not method_name == 'close'
    }
    for method_name, method in convertables.items():
        new_method = p2p_convertion_function(method_name)
        setattr(cls, method_name, new_method)
    return cls


@default_dont_communicate_return
class SimulationConsumerAbstract(mp.Process):
    _id = 0
    """This class sole purpose is to better 'hide' all interprocess related code
    from the user."""
    def __init__(self, process_io, scene="", gui=False):
        super().__init__(
            name="simulation_consumer_{}".format(SimulationConsumerAbstract._id)
        )
        self._id = SimulationConsumerAbstract._id
        SimulationConsumerAbstract._id += 1
        self._scene = scene
        self._gui = gui
        self._process_io = process_io
        np.random.seed()

    def run(self):
        self._pyrep = PyRep()
        self._pyrep.launch(
            self._scene,
            headless=not self._gui,
            write_coppeliasim_stdout_to_file=True
        )
        self._process_io["simulaton_ready"].set()
        self._main_loop()

    def _close_pipes(self):
        self._process_io["command_pipe_out"].close()
        self._process_io["return_value_pipe_in"].close()
        # self._process_io["exception_pipe_in"].close() # let this one open

    def _main_loop(self):
        success = True
        while success and not self._process_io["must_quit"].is_set():
            success = self._consume_command()
        self._pyrep.shutdown()
        self._close_pipes()

    def _consume_command(self):
        try: # to execute the command and send result
            success = True
            command = self._process_io["command_pipe_out"].recv()
            self._process_io["slot_in_command_queue"].release()
            ret = command[0](self, *command[1], **command[2])
            if command[0]._communicate_return_value:
                self._communicate_return_value(ret)
        except Exception as e: # print traceback, dont raise
            traceback = format_exc()
            success = False # return False: quit the main loop
            self._process_io["exception_pipe_in"].send((e, traceback))
        finally:
            return success

    def _communicate_return_value(self, value):
        self._process_io["return_value_pipe_in"].send(value)

    def signal_command_pipe_empty(self):
        self._process_io["command_pipe_empty"].set()
        time.sleep(0.1)
        self._process_io["command_pipe_empty"].clear()

    def good_bye(self):
        pass


@default_dont_communicate_return
class SimulationConsumer(SimulationConsumerAbstract):
    def __init__(self, process_io, scene="", gui=False):
        super().__init__(process_io, scene, gui)
        self._shapes = defaultdict(list)
        self._stateful_shape_list = []
        self._arm_list = []
        self._state_buffer = None
        self._cams = {}

    @communicate_return_value
    def get_state(self):
        n = self._n_joints
        if self._state_buffer is None:
            n_reg = self.get_n_registers()
            size = 3 * n + n_reg
            self._state_buffer = np.zeros(shape=size, dtype=np.float32)
            self._state_mean = np.zeros(shape=size, dtype=np.float32)
            self._state_std = np.zeros(shape=size, dtype=np.float32)
            self._state_mean[3 * n:] = 0.5
            # scaling with values measured from random movements
            pos_std = [1.6, 1.3, 1.6, 1.3, 2.2, 1.7, 2.3]
            spe_std = [1.1, 1.2, 1.4, 1.3, 2.4, 1.7, 2.1]
            for_std = [91, 94, 43, 67, 12, 8.7, 2.3]
            reg_std = [0.5 for i in range(n_reg)]
            self._state_std[0 * n:1 * n] = np.tile(pos_std, n // 7)
            self._state_std[1 * n:2 * n] = np.tile(spe_std, n // 7)
            self._state_std[2 * n:3 * n] = np.tile(for_std, n // 7)
            self._state_std[3 * n:] = reg_std
        self._state_buffer[0 * n:1 * n] = self.get_joint_positions()
        self._state_buffer[1 * n:2 * n] = self.get_joint_velocities()
        self._state_buffer[2 * n:3 * n] = self.get_joint_forces()
        self._state_buffer[3 * n:] = self.get_stateful_objects_states()
        # STATE NORMALIZATION:
        self._state_buffer -= self._state_mean
        self._state_buffer /= self._state_std
        return self._state_buffer

    @communicate_return_value
    def get_joint_positions(self):
        last = 0
        next = 0
        for arm, joint_count in zip(self._arm_list, self._arm_joints_count):
            next += joint_count
            self._arm_joints_positions_buffer[last:next] = \
                arm.get_joint_positions()
            last = next
        return self._arm_joints_positions_buffer

    @communicate_return_value
    def get_joint_velocities(self):
        last = 0
        next = 0
        for arm, joint_count in zip(self._arm_list, self._arm_joints_count):
            next += joint_count
            self._arm_joints_velocities_buffer[last:next] = \
                arm.get_joint_velocities()
            last = next
        return self._arm_joints_velocities_buffer

    def set_joint_target_velocities(self, velocities):
        last = 0
        next = 0
        for arm, joint_count in zip(self._arm_list, self._arm_joints_count):
            next += joint_count
            arm.set_joint_target_velocities(velocities[last:next])
            last = next

    @communicate_return_value
    def apply_action(self, actions):
        velocities = actions * self._upper_velocity_limits
        self.set_joint_target_velocities(velocities)
        self.step_sim()
        return self.get_data()

    def set_control_loop_enabled(self, bool):
        for arm in self._arm_list:
            arm.set_control_loop_enabled(bool)

    def set_motor_locked_at_zero_velocity(self, bool):
        for arm in self._arm_list:
            arm.set_motor_locked_at_zero_velocity(bool)

    @communicate_return_value
    def get_joint_forces(self):
        last = 0
        next = 0
        for arm, joint_count in zip(self._arm_list, self._arm_joints_count):
            next += joint_count
            self._arm_joints_torques_buffer[last:next] = \
                arm.get_joint_forces()
            last = next
        return self._arm_joints_torques_buffer

    def set_joint_forces(self, forces):
        last = 0
        next = 0
        for arm, joint_count in zip(self._arm_list, self._arm_joints_count):
            next += joint_count
            arm.set_joint_forces(forces[last:next])
            last = next

    def step_sim(self):
        self._pyrep.step()

    def start_sim(self):
        self._pyrep.start()

    def stop_sim(self):
        self._pyrep.stop()

    @communicate_return_value
    def get_simulation_timestep(self):
        return self._pyrep.get_simulation_timestep()


@consumer_to_producer_method_conversion
class SimulationProducer(object):
    def __init__(self, scene="", gui=False):
        self._process_io = {}
        self._process_io["must_quit"] = mp.Event()
        self._process_io["simulaton_ready"] = mp.Event()
        self._process_io["command_pipe_empty"] = mp.Event()
        self._process_io["slot_in_command_queue"] = mp.Semaphore(100)
        pipe_out, pipe_in = mp.Pipe(duplex=False)
        self._process_io["command_pipe_in"] = pipe_in
        self._process_io["command_pipe_out"] = pipe_out
        pipe_out, pipe_in = mp.Pipe(duplex=False)
        self._process_io["return_value_pipe_in"] = pipe_in
        self._process_io["return_value_pipe_out"] = pipe_out
        pipe_out, pipe_in = mp.Pipe(duplex=False)
        self._process_io["exception_pipe_in"] = pipe_in
        self._process_io["exception_pipe_out"] = pipe_out
        self._consumer = SimulationConsumer(self._process_io, scene, gui=gui)
        self._consumer.start()
        print("consumer {} started".format(self._consumer._id))
        self._closed = False
        # atexit.register(self.close)

    def _get_process_io(self):
        return self._process_io

    def _check_consumer_alive(self):
        if not self._consumer.is_alive():
            self._consumer.join()
            print("### My friend ({}) died ;( raising its exception: ###\n".format(self._consumer._id))
            self._consumer.join()
            self._closed = True
            exc, traceback = self._process_io["exception_pipe_out"].recv()
            raise SimulationConsumerFailed(exc, traceback)
        return True

    def _send_command(self, function, *args, **kwargs):
        self._process_io["command_pipe_in"].send((function, args, kwargs))
        semaphore = self._process_io["slot_in_command_queue"]
        while not semaphore.acquire(block=False, timeout=0.1):
            self._check_consumer_alive()

    def _wait_for_answer(self):
        while not self._process_io["return_value_pipe_out"].poll(1):
            # print(method, "waiting for an answer...nothing yet...alive?")
            self._check_consumer_alive()
        answer = self._process_io["return_value_pipe_out"].recv()
        # print(method, "waiting for an answer...got it!")
        return answer

    def _wait_consumer_ready(self):
        self._process_io["simulaton_ready"].wait()

    def close(self):
        if not self._closed:
            # print("Producer closing")
            if self._consumer.is_alive():
                self._wait_command_pipe_empty()
                # print("command pipe empty, setting must_quit flag")
                self._process_io["must_quit"].set()
                # print("flushing command pipe")
                self.good_bye()
            self._closed = True
            # print("succesfully closed")
            self._consumer.join()
            print("consumer {} closed".format(self._consumer._id))
        else:
            print("{} already closed, doing nothing".format(self._consumer._id))

    def _wait_command_pipe_empty(self):
        self._send_command(SimulationConsumer.signal_command_pipe_empty)
        self._process_io["command_pipe_empty"].wait()

    def __del__(self):
        self.close()


@producer_to_pool_method_convertion
class SimulationPool:
    def __init__(self, size, scene="", guis=[]):
        self._producers = [
            SimulationProducer(scene, gui=i in guis) for i in range(size)
        ]
        self._active_producers_indices = list(range(size))
        self._distribute_args_mode = False
        self.wait_consumer_ready()

    @contextmanager
    def specific(self, list_or_int):
        _active_producers_indices_before = self._active_producers_indices
        indices = list_or_int if type(list_or_int) is list else [list_or_int]
        self._active_producers_indices = indices
        yield
        self._active_producers_indices = _active_producers_indices_before

    @contextmanager
    def distribute_args(self):
        self._distribute_args_mode = True
        yield
        self._distribute_args_mode = False

    def _get_active_producers(self):
        return [self._producers[i] for i in self._active_producers_indices]
    _active_producers = property(_get_active_producers)

    def close(self):
        for producer in self._producers:
            producer.close()

    def wait_consumer_ready(self):
        for producer in self._producers:
            producer._wait_consumer_ready()



if __name__ == '__main__':
    def test_1():
        scene = "/home/aecgroup/aecdata/Software/vrep_scenes/stereo_vision_robot_collection.ttt"
        simulation = SimulationProducer(scene, gui=True)
        simulation.start_sim()
        for i in range(1000):
            print(i)
            simulation.step_sim()
        simulation.stop_sim()

    test_1()
