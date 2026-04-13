"""Terminal plots for injection profiles using plotext."""

import plotext as plt


def plot_profile(name: str, min_frac: list[float], max_frac: list[float], current_hour: int, max_watts: float = 800.0) -> None:
    """Plot a 24h min/max injection profile in the terminal."""
    min_w = [v * max_watts for v in min_frac]
    max_w = [v * max_watts for v in max_frac]
    hours = list(range(24))
    plt.clf()
    plt.plot(hours, min_w, color="blue+")
    plt.plot(hours, max_w, color="orange")
    plt.vline(current_hour, color="red")
    plt.plotsize(78, 10)
    plt.xticks(list(range(0, 24, 3)))
    plt.ylim(0, 400)
    plt.yticks([0, 200, 400])
    plt.title(name)
    plt.show()
