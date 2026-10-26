from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Label,
    ProgressBar,
    RichLog,
    Static,
    Switch,
)


# ---------------------------------------------------------
# Shared components
# ---------------------------------------------------------


class ScreenTitle(Static):
    def __init__(self, number: int, title: str, description: str):
        super().__init__(
            f"[b]Screen {number}: {title}[/b]\n"
            f"[dim]{description}[/dim]",
            classes="screen-title",
        )


class BaseHILScreen(Screen):
    """Common layout/behavior shared by HILTester screens."""

    SCREEN_NUMBER = 0
    SCREEN_NAME = ""
    DESCRIPTION = ""

    def __init__(self, project_index: int | None = None):
        super().__init__()
        self.project_index = project_index

    @property
    def project(self):
        if self.project_index is None:
            return None
        return self.app.projects[self.project_index]

    def compose_header(self) -> ComposeResult:
        yield Header()

        yield ScreenTitle(
            self.SCREEN_NUMBER,
            self.SCREEN_NAME,
            self.DESCRIPTION,
        )

    def compose_screen_toggle(self) -> ComposeResult:
        if self.project is None:
            return

        enabled = self.project.screen_enabled.get(
            self.SCREEN_NUMBER,
            True,
        )

        with Horizontal(classes="toggle-row"):
            yield Label("Screen Enabled")
            yield Switch(
                value=enabled,
                id="screen_enabled",
            )

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if (
            event.switch.id == "screen_enabled"
            and self.project is not None
        ):
            self.project.screen_enabled[self.SCREEN_NUMBER] = event.value

    def back_button(self) -> Button:
        return Button(
            "← Back",
            id="back",
            variant="default",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.app.pop_screen()


# ---------------------------------------------------------
# SCREEN 1 - HOME
# ---------------------------------------------------------


class HomeScreen(Screen):
    """Main application dashboard / Screen 1."""

    def compose(self) -> ComposeResult:
        yield Header()

        with VerticalScroll(id="home-container"):
            yield Static(
                "[b]HILTester[/b]\n"
                "[dim]Hardware-in-the-Loop Project Dashboard[/dim]",
                id="home-title",
            )

            with Horizontal(id="project-actions"):
                yield Button(
                    "+ Create New Project",
                    id="create_project",
                    variant="primary",
                )

                yield Button(
                    "Load Existing Project",
                    id="load_project",
                )

            yield Static(
                "PROJECTS",
                classes="section-title",
            )

            with Container(id="projects-list"):
                for index in range(3):
                    yield from self._compose_project_slot(index)

            yield Static(
                "SYSTEM TOOLS",
                classes="section-title",
            )

            with Horizontal(id="system-tools"):
                yield Button(
                    "Failsafe Monitor",
                    id="failsafe",
                )

                yield Button(
                    "Knowledge Base",
                    id="knowledge",
                )

        yield Footer()

    def _compose_project_slot(self, index: int):
        if index < len(self.app.projects):
            project = self.app.projects[index]

            deploy_status = (
                "[green]● Control Room Ready[/green]"
                if project.deployment_complete
                else "[dim]● Not Deployed[/dim]"
            )

            with Container(classes="project-card"):

                with Horizontal(classes="project-card-content"):

                    with Vertical(classes="project-info"):
                        yield Static(
                            f"[b]{project.name}[/b]",
                            classes="project-name",
                        )

                        yield Static(
                            deploy_status,
                            classes="project-status",
                        )

                    with Vertical(classes="project-buttons"):
                        yield Button(
                            "Open Project",
                            id=f"open_project_{index}",
                            variant="primary",
                        )

                        yield Button(
                            "Control Room",
                            id=f"control_room_{index}",
                            variant="success",
                            disabled=not project.deployment_complete,
                        )

        else:
            with Container(classes="project-card empty-project"):
                yield Static(
                    f"[dim]Project Slot {index + 1} — Empty[/dim]"
                )

    def on_screen_resume(self) -> None:
        self.recompose()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id

        if button_id == "create_project":
            if len(self.app.projects) >= 3:
                self.notify(
                    "Maximum of 3 projects currently supported.",
                    severity="warning",
                )
                return

            project_number = len(self.app.projects) + 1

            from app import ProjectState

            self.app.projects.append(
                ProjectState(
                    name=f"HIL Project {project_number}"
                )
            )

            self.recompose()
            return

        if button_id == "load_project":
            self.notify(
                "Load Project placeholder",
                title="Not Connected Yet",
            )
            return

        if button_id == "failsafe":
            self.app.push_screen(FailsafeScreen())
            return

        if button_id == "knowledge":
            self.app.push_screen(KnowledgeBaseScreen())
            return

        if button_id.startswith("open_project_"):
            index = int(button_id.rsplit("_", 1)[1])

            self.app.push_screen(
                ProjectHubScreen(index)
            )
            return

        if button_id.startswith("control_room_"):
            index = int(button_id.rsplit("_", 1)[1])

            project = self.app.projects[index]

            if project.deployment_complete:
                self.app.push_screen(
                    ControlRoomScreen(index)
                )


# ---------------------------------------------------------
# PROJECT HUB
# ---------------------------------------------------------


class ProjectHubScreen(Screen):
    """
    Project-specific navigation area.

    Screens 2, 3 and 4 can be completed in any order.
    Screen 5 unlocks after all three are confirmed.
    """

    def __init__(self, project_index: int):
        super().__init__()
        self.project_index = project_index

    @property
    def project(self):
        return self.app.projects[self.project_index]

    def compose(self) -> ComposeResult:
        yield Header()

        with VerticalScroll(classes="main-container"):
            yield Static(
                f"[b]{self.project.name}[/b]\n"
                "[dim]Project Configuration[/dim]",
                classes="screen-title",
            )

            yield Static(
                "SETUP",
                classes="section-title",
            )

            with Container(classes="navigation-panel"):

                yield from self._navigation_item(
                    2,
                    "Pi & Network Setup",
                    "Configure Raspberry Pi connectivity and remote access.",
                    "network",
                )

                yield from self._navigation_item(
                    3,
                    "Source Control & GitHub",
                    "Select and verify the deployment repository.",
                    "github",
                )

                yield from self._navigation_item(
                    4,
                    "Hardware Topology",
                    "Discover and confirm connected HIL hardware.",
                    "topology",
                )

            yield Static(
                "DEPLOYMENT",
                classes="section-title",
            )

            all_confirmed = all(
                self.project.confirmations.values()
            )

            with Container(classes="navigation-panel"):
                yield Button(
                    "Screen 5 • Deploy HIL Pi",
                    id="deploy",
                    variant="success",
                    disabled=not all_confirmed,
                )

                if not all_confirmed:
                    yield Static(
                        "[dim]Confirm Screens 2, 3 and 4 "
                        "to unlock deployment.[/dim]",
                        classes="hint",
                    )

            if self.project.deployment_complete:
                yield Button(
                    "Open Testing Control Room",
                    id="control_room",
                    variant="success",
                    classes="large-action",
                )

            yield Button(
                "← Home",
                id="back",
            )

        yield Footer()

    def _navigation_item(
        self,
        screen_number: int,
        name: str,
        description: str,
        button_id: str,
    ):
        confirmed = self.project.confirmations[
            screen_number
        ]

        status = (
            "[green]● Confirmed[/green]"
            if confirmed
            else "[yellow]● Needs Confirmation[/yellow]"
        )

        with Container(classes="navigation-item"):
            with Horizontal(classes="navigation-item-row"):

                with Vertical(classes="navigation-label"):
                    yield Static(
                        f"[b]Screen {screen_number}: {name}[/b]",
                    )

                    yield Static(
                        f"[dim]{description}[/dim]",
                    )

                    yield Static(
                        status,
                    )

                yield Button(
                    "Open",
                    id=button_id,
                    variant="primary",
                )

    def on_screen_resume(self) -> None:
        self.recompose()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id

        if button_id == "back":
            self.app.pop_screen()

        elif button_id == "network":
            self.app.push_screen(
                NetworkScreen(self.project_index)
            )

        elif button_id == "github":
            self.app.push_screen(
                GitHubScreen(self.project_index)
            )

        elif button_id == "topology":
            self.app.push_screen(
                TopologyScreen(self.project_index)
            )

        elif button_id == "deploy":
            self.app.push_screen(
                DeployScreen(self.project_index)
            )

        elif button_id == "control_room":
            self.app.push_screen(
                ControlRoomScreen(self.project_index)
            )

# ---------------------------------------------------------
# CONFIRMABLE PROJECT SCREEN
# ---------------------------------------------------------


class ConfirmableProjectScreen(BaseHILScreen):
    """
    Base class for Screens 2, 3 and 4.

    These screens must be confirmed before deployment.
    """

    ACTION_LABEL = None
    ACTION_ID = None

    def compose(self) -> ComposeResult:
        yield from self.compose_header()

        with Container(classes="main-container"):

            yield from self.compose_screen_toggle()

            with Container(classes="placeholder-panel"):
                yield Static(self.placeholder_text())

            if self.ACTION_LABEL and self.ACTION_ID:
                yield Button(
                    self.ACTION_LABEL,
                    id=self.ACTION_ID,
                    variant="primary",
                )

            confirmed = self.project.confirmations[
                self.SCREEN_NUMBER
            ]

            yield Button(
                "✓ Confirmed"
                if confirmed
                else "Confirm Configuration",
                id="confirm",
                variant="success",
            )

            yield self.back_button()

        yield Footer()

    def placeholder_text(self) -> str:
        return (
            "[dim]Functionality placeholder.\n\n"
            "Backend integration will be added later.[/dim]"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":

            self.project.confirmations[
                self.SCREEN_NUMBER
            ] = True

            event.button.label = "✓ Confirmed"

            self.notify(
                f"Screen {self.SCREEN_NUMBER} confirmed."
            )

            return

        super().on_button_pressed(event)


# ---------------------------------------------------------
# SCREEN 2
# ---------------------------------------------------------


class NetworkScreen(ConfirmableProjectScreen):
    SCREEN_NUMBER = 2
    SCREEN_NAME = "Pi & Network Setup"
    DESCRIPTION = "Configure Raspberry Pi connectivity and remote access."
    ACTION_LABEL = "Check Connection"
    ACTION_ID = "check_connection"

    def placeholder_text(self) -> str:
        return """
[b]Connection Status[/b]

LAN Status:
[red]● Offline[/red]

Tailscale:
[yellow]● Not Checked[/yellow]

Target Raspberry Pi:
[dim]● Not discovered[/dim]

Tailscale IP:
[dim]Not discovered[/dim]

SSH:
[yellow]● Not checked[/yellow]

────────────────────────────

Future functionality:
• Network adapter discovery
• Tailscale device discovery
• Tailscale IP discovery
• SSH connectivity test
"""


# ---------------------------------------------------------
# SCREEN 3
# ---------------------------------------------------------


class GitHubScreen(ConfirmableProjectScreen):
    SCREEN_NUMBER = 3
    SCREEN_NAME = "Source Control & GitHub"
    DESCRIPTION = "Select and verify the HILTester repository."
    ACTION_LABEL = "Verify Repository"
    ACTION_ID = "verify_repository"

    def placeholder_text(self) -> str:
        return """
[b]GitHub Repository[/b]

Repository:
dprime999/HILTester

Branch:
Default

Access:
[yellow]● Not Checked[/yellow]

────────────────────────────

Future functionality:
• Verify repository access
• Discover default branch
• Repository clone/update
"""


# ---------------------------------------------------------
# SCREEN 4
# ---------------------------------------------------------


class TopologyScreen(ConfirmableProjectScreen):
    SCREEN_NUMBER = 4
    SCREEN_NAME = "Hardware Topology"
    DESCRIPTION = "Discover and confirm connected HIL hardware."
    ACTION_LABEL = "Scan Hardware"
    ACTION_ID = "scan_hardware"

    def placeholder_text(self) -> str:
        return """
[b]Hardware Discovery[/b]

Expected Hardware:
[dim]No hardware profile saved.[/dim]

────────────────────────────

Connected USB Devices:
[dim]Run a scan to discover hardware.[/dim]

────────────────────────────

Future functionality:
• Scan connected USB devices
• Display discovered VID/PID automatically
• Save selected devices as the expected rig
• Rescan and verify saved hardware
"""


# ---------------------------------------------------------
# SCREEN 5
# ---------------------------------------------------------


class DeployScreen(BaseHILScreen):
    SCREEN_NUMBER = 5
    SCREEN_NAME = "One-Click HIL Pi Deployer"
    DESCRIPTION = "Prepare and launch the HIL runtime environment."

    def compose(self) -> ComposeResult:
        yield from self.compose_header()

        with Container(classes="main-container"):

            yield from self.compose_screen_toggle()

            with Container(classes="placeholder-panel"):
                yield Static(
                    "[b]Deployment Pipeline[/b]\n\n"
                    "○ Connect to Pi via SSH\n"
                    "○ Verify Docker\n"
                    "○ Clone / update repository\n"
                    "○ Find Docker Compose configuration\n"
                    "○ Pull container image\n"
                    "○ Launch runtime\n"
                    "○ Verify container status"
                )

                yield ProgressBar(
                    total=100,
                    show_eta=False,
                    id="deploy_progress",
                )

            yield Button(
                "Deploy HILTester",
                id="complete_deploy",
                variant="success",
            )

            yield self.back_button()

        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "complete_deploy":

            progress = self.query_one(
                "#deploy_progress",
                ProgressBar,
            )

            progress.update(progress=100)

            self.project.deployment_complete = True

            self.notify(
                "Deployment complete. Control Room unlocked.",
                title="HIL Runtime Ready",
            )

            event.button.label = "✓ Deployment Complete"
            event.button.disabled = True

            return

        super().on_button_pressed(event)


# ---------------------------------------------------------
# SCREEN 6
# ---------------------------------------------------------


class ControlRoomScreen(BaseHILScreen):
    SCREEN_NUMBER = 6
    SCREEN_NAME = "Testing Control Room"
    DESCRIPTION = "Run and monitor HIL testing operations."

    def compose(self) -> ComposeResult:
        yield from self.compose_header()

        with Container(classes="main-container"):

            yield from self.compose_screen_toggle()

            with Horizontal(classes="control-buttons"):
                yield Button(
                    "START TEST",
                    id="start_test",
                    variant="success",
                )

                yield Button(
                    "STOP TEST",
                    id="stop_test",
                    variant="error",
                )

            yield ProgressBar(
                total=100,
                show_eta=False,
            )

            yield Static(
                "Live Test Telemetry",
                classes="section-title",
            )

            yield RichLog(
                id="test_log",
                classes="log-window",
            )

            with Horizontal(classes="control-room-links"):
                yield Button(
                    "Open Workspace IDE",
                    id="ide",
                )

                yield Button(
                    "AI Copilot",
                    id="copilot",
                )

            yield self.back_button()

        yield Footer()

    def on_mount(self) -> None:
        log = self.query_one("#test_log", RichLog)

        log.write(
            "[dim]HILTester Control Room initialized.[/dim]"
        )
        log.write(
            "[dim]Awaiting backend test runtime...[/dim]"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ide":
            self.app.push_screen(
                IDEIntegratorScreen(self.project_index)
            )
            return

        if event.button.id == "copilot":
            self.app.push_screen(
                AICopilotScreen(self.project_index)
            )
            return

        if event.button.id in ("start_test", "stop_test"):
            self.notify(
                "Test controls are placeholders for now."
            )
            return

        super().on_button_pressed(event)


# ---------------------------------------------------------
# GLOBAL PLACEHOLDER SCREEN
# ---------------------------------------------------------


class GlobalPlaceholderScreen(BaseHILScreen):

    PLACEHOLDER = ""

    def compose(self) -> ComposeResult:
        yield from self.compose_header()

        with Container(classes="main-container"):

            if self.project is not None:
                yield from self.compose_screen_toggle()

            with Container(classes="placeholder-panel"):
                yield Static(self.PLACEHOLDER)

            yield self.back_button()

        yield Footer()


# ---------------------------------------------------------
# SCREEN 7
# ---------------------------------------------------------


class FailsafeScreen(GlobalPlaceholderScreen):
    SCREEN_NUMBER = 7
    SCREEN_NAME = "Failsafe & System Resiliency Monitor"
    DESCRIPTION = "Monitor HIL system health and connectivity."

    PLACEHOLDER = """
[b]System Health[/b]

Pi Heartbeat
[yellow]● Not Running[/yellow]

USB Devices
[yellow]● Not Monitored[/yellow]

Runtime Safety State
[green]● SAFE[/green]

────────────────────────────

Future:
• Heartbeat monitoring
• Device disconnect detection
• Automatic test freeze
• Diagnostic timestamps
"""


# ---------------------------------------------------------
# SCREEN 8
# ---------------------------------------------------------


class IDEIntegratorScreen(GlobalPlaceholderScreen):
    SCREEN_NUMBER = 8
    SCREEN_NAME = "Integrated Workspace IDE"
    DESCRIPTION = "Open project source code in an external editor."

    PLACEHOLDER = """
[b]Workspace Editor[/b]

Preferred Editor:
[dim]Not detected[/dim]

Workspace:
[dim]HILTester repository[/dim]

Advanced Mode:
OFF

[ Open Workspace in Editor ]
"""


# ---------------------------------------------------------
# SCREEN 9
# ---------------------------------------------------------


class KnowledgeBaseScreen(GlobalPlaceholderScreen):
    SCREEN_NUMBER = 9
    SCREEN_NAME = "Knowledge Base & Quickstart"
    DESCRIPTION = "Documentation, onboarding, and reference material."

    PLACEHOLDER = """
[b]HILTester Knowledge Base[/b]

Quick Start

1. Create or load a project
2. Configure network
3. Connect GitHub workspace
4. Configure hardware topology
5. Deploy HIL environment
6. Start testing

────────────────────────────

README.md rendering will live here later.
"""


# ---------------------------------------------------------
# SCREEN 10
# ---------------------------------------------------------


class AICopilotScreen(GlobalPlaceholderScreen):
    SCREEN_NUMBER = 10
    SCREEN_NAME = "AI Copilot Terminal"
    DESCRIPTION = "Future AI-assisted HIL diagnostics and optimization."

    PLACEHOLDER = """
[b]AI Copilot[/b]

Provider:
○ OpenAI
○ Anthropic
○ Local LLM

API Status:
[dim]Not Configured[/dim]

────────────────────────────

Future context sources:

• Test logs
• topology.json
• UART telemetry
• Docker runtime
• Hardware diagnostics
"""
