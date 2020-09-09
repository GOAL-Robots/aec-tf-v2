import matplotlib.pyplot as plt
from mpl_toolkits.axes_grid1.inset_locator import inset_axes, InsetPosition
import numpy as np


class FigureManager:
    def __init__(self, filepath, save=True):
        self._fig = plt.figure(dpi=200)
        self._filepath = filepath
        self._save = save

    def __enter__(self):
        return self._fig

    def __exit__(self, exc_type, exc_value, exc_traceback):
        if self._save:
            print("saving plot {}  ...  ".format(self._filepath), end="")
            self._fig.savefig(self._filepath)
            print("done")
            plt.close(self._fig)
        else:
            plt.show()


def recerr_wrt_error(ax, errors, reconstruction_errors,
        ylim=[0, 0.04], title=None, xlabel=None, ylabel=None, inset=True, legend=False):
    for e, r in zip(errors, reconstruction_errors):
        ax.plot(e, r, 'b-', alpha=0.6, linewidth=1)
    mean = np.mean(reconstruction_errors, axis=0)
    ax.plot(e, mean, 'r-', linewidth=3, label="mean")
    ax.axvline(0, color="k", linestyle="--")
    if inset:
        axins = inset_axes(ax, width="20%", height="20%", borderpad=2)
        axins.plot(e, mean, 'r-')
        axins.axvline(0, color="k", linestyle="--", alpha=0.5)
        axins.set_xticks([])
        axins.set_yticks([])
        axins.set_title("Mean only")
    #
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_ylim(ylim)
    if ylabel is None:
        ax.set_yticks([])
    if legend:
        ax.legend()


def action_wrt_error(ax, errors, actions, yscale, title=None, xlabel=None, ylabel=None, nbins=25, range=(-1, 1)):
    histograms = []
    edges = []
    for err, acts in zip(errors, actions):
        hist, edg = np.histogram(acts, bins=nbins, density=True, range=range)
        histograms.append(hist)
        edges.append(edg)
    image = np.zeros((len(hist), len(histograms)))
    for i, hist in enumerate(histograms):
        image[:, i] = hist
    ax.imshow(image, origin="lower", extent=(errors[0], errors[-1], -yscale, yscale), aspect="auto")
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    if ylabel is None:
        ax.set_yticks([])


def data_wrt_episode(ax, data, std=True, ylim=[-5, 5], title=None, xlabel=None, ylabel=None):
    mean = np.mean(data, axis=0)
    x = np.arange(len(mean))
    ax.plot(x, mean, 'b-')
    if std:
        std = np.std(data, axis=0)
        ax.fill_between(x, mean - std, mean + std, color='b', alpha=0.4)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    if ylabel is None:
        ax.set_yticks([])


def critic_error_wrt_episode(ax, critic, recerr, title=None, xlabel=None, ylabel=None):
    critic = critic[..., :-1]
    true_critic = (recerr[..., :-1] - recerr[..., 1:]) * 600
    # print(recerr)
    # print(true_critic)
    x = np.arange(critic.shape[-1])
    a = 0
    b = 0
    for critic_one_error, true_critic_one_error in zip(critic, true_critic):
        total = 4
        sub_ax = inset_axes(ax, height="100%", width="100%", bbox_to_anchor=(0.05, a / len(critic) + 0.015, 1.00, 1 / len(critic)), bbox_transform=ax.transAxes)
        if b != 0:
            sub_ax.set_yticks([])
        sub_ax.set_xticks([])
        for critic_one_error_one_stimulus, true_critic_one_error_one_stimulus in zip(critic_one_error, true_critic_one_error):
            sub_ax.fill_between(x, critic_one_error_one_stimulus, true_critic_one_error_one_stimulus, color='b', alpha=0.1)
            sub_ax.plot(x, critic_one_error_one_stimulus, 'r-', alpha=0.1)
            total -= 1
            if total == 0:
                break
            b += 1
        a += 1
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_yticks([])
    ax.set_xticks([])


def scatter_wrt_delta_error(ax, data, error, title=None, xlabel=None, ylabel=None):
    delta_error = error[..., :-1] - error[..., 1:]
    ax.scatter(delta_error.flatten(), data.flatten(), alpha=0.2, s=1)
    try:
        a, b = np.polyfit(delta_error.flatten(), data.flatten(), 1)
        mini = np.min(delta_error)
        maxi = np.max(delta_error)
        ax.plot([mini, maxi], [a * mini + b, a * maxi + b], 'r-')
    except np.linalg.LinAlgError:
        pass
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    # if ylabel is None:
    #     ax.set_yticks([])


if __name__ == '__main__':
    from test_data import TestDataContainer

    path = "../experiments/2020-09-09/13-23-05/job3_agent.exploration.prob.0.05__agent.exploration.stddev.0.25__agent.policy_learning_rate.1e-05__procedure.action_scaling.2.2.25/tests/default_at_2m_009980.pkl"
    plot_path = "/tmp/plot/"
    save = True

    test_data = TestDataContainer.load(path)
    test_data.plot(plot_path, save=save)
