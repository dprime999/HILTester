from dataclasses import dataclass, field

from textual.app import App

from screens import HomeScreen


@dataclass
class ProjectState:
    name: str
    confirmations: dict[int, bool] = field(
        default_factory=lambda: {
            2: False,
            3: False,
            4: False,
        }
    )

    deployment_complete: bool = False

    screen_enabled: dict[int, bool] = field(
        default_factory=lambda: {
            2: True,
            3: True,
            4: True,
            5: True,
            6: True,
            7: True,
            8: True,
            9: True,
            10: True,
        }
    )


class HILTesterApp(App):
    TITLE = "HILTester"
    SUB_TITLE = "Hardware-in-the-Loop Control Center"

    CSS_PATH = "styles.tcss"

    def __init__(self):
        super().__init__()

        # Temporary in-memory project storage.
        # Later this can come from project JSON/config files.
        self.projects: list[ProjectState] = [
            ProjectState("STM32 Development Rig"),
            ProjectState("Pico Test Bench"),
        ]

    def on_mount(self) -> None:
        self.push_screen(HomeScreen())


if __name__ == "__main__":
    HILTesterApp().run()