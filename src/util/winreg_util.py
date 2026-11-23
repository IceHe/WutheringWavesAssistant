import logging
import winreg

from src.util.hwnd_util import get_ww_exe_path

logger = logging.getLogger(__name__)

_LAUNCHER_KEY = r"SOFTWARE\kurogame\KRLauncher"
_UNINSTALL_KEY = r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
# A launcher migration can leave the old uninstall entry pointing to an empty install.
_GAME_REGISTRY_PATHS = (
    (winreg.HKEY_CURRENT_USER, _LAUNCHER_KEY + r"\Aki_G152_default_10003", "SingleLauncherInstallPath"),
    (winreg.HKEY_LOCAL_MACHINE, _UNINSTALL_KEY + r"\KRInstall Wuthering Waves", "InstallPath"),
    (winreg.HKEY_CURRENT_USER, _LAUNCHER_KEY + r"\Aki_G153_default_50004", "SingleLauncherInstallPath"),
    (winreg.HKEY_LOCAL_MACHINE, _UNINSTALL_KEY + r"\KRInstall Wuthering Waves Overseas", "InstallPath"),
)


def get_install_path() -> str | None:
    for hive, key_name, value_name in _GAME_REGISTRY_PATHS:
        try:
            with winreg.OpenKey(hive, key_name) as key:
                install_path, _ = winreg.QueryValueEx(key, value_name)
        except OSError:
            logger.debug("Could not read %s from %s", value_name, key_name, exc_info=True)
            continue
        if isinstance(install_path, str):
            if executable := get_ww_exe_path(install_path):
                logger.debug("Resolved game executable from %s: %s", key_name, executable)
                return executable
            logger.debug("No installed game executable at %s", install_path)
    return None
