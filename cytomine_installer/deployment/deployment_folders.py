from abc import ABC, abstractmethod
import os
import yaml
import shutil

from cytomine_installer.deployment.env_store import MergeEnvStorePolicy
from cytomine_installer.deployment.installer_config import InstallerConfig, UpdatePolicy
from .deployment_files import (
  DOCKER_COMPOSE_FILENAME,
  ConfigFile,
  DockerComposeFile,
  EditableDockerCompose,
)
from .deployment_files import DOCKER_COMPOSE_OVERRIDE_FILENAME
from .errors import InvalidServerConfigurationError
from ..util import list_relative_files, write_dotenv


class Deployable(ABC):
  @abstractmethod
  def deploy_files(self, target_directory):
    """Generates/transfers a set of files in the target directory."""
    pass

  @abstractmethod
  def clean_generated_files(self, target_directory):
    """Clean files generated by deploy_files"""
    pass

  @property
  @abstractmethod
  def source_files(self):
    """List (existing) source files"""
    pass

  @property
  def target_files(self):
    """List files that would be deployed by the Deployable (relative path)"""
    files = list()
    files.extend(self.source_files)
    files.extend(self.generated_files)
    return files

  @property
  @abstractmethod
  def generated_files(self):
    """List files that would be generated by the Deployable (relative path)"""
    pass


class ServerFolder(Deployable):
  def __init__(
    self,
    server_name,
    directory,
    envs: ConfigFile,
    configs_folder="configs",
    envs_folder="envs",
    configs_mount_point="/cm_configs",
  ) -> None:
    """
    Parameters:
    -----------
    server_name: str
    directory: str
      Server directory path
    configs_folder: str
      Name of the configs folder
    envs_folder:
      Name of the target environment folder
    configs_mount_point:
      Name of the configuration target folder within the container
    """
    self._server_name = server_name
    self._directory = directory
    self._configs_folder = configs_folder
    self._configs_mount_point = configs_mount_point
    self._envs_folder = envs_folder
    self._docker_compose_file = DockerComposeFile(directory)
    self._envs = envs

  @property
  def directory(self):
    return self._directory

  @property
  def server_name(self):
    return self._server_name

  @property
  def has_config(self):
    return os.path.exists(self.configs_path)

  @property
  def configs_path(self):
    return os.path.join(self._directory, self._configs_folder)

  @property
  def docker_compose_path(self):
    return os.path.join(self._directory, DOCKER_COMPOSE_FILENAME)

  @property
  def source_files(self):
    """List (existing) source files"""
    files = list()
    files.append(os.path.relpath(self._docker_compose_file.filepath, self._directory))
    config_files = list_relative_files(os.path.join(self._directory, self._configs_folder))
    for config_file in config_files:
      files.append(os.path.join(self._configs_folder, config_file))
    return files

  @property
  def generated_files(self):
    target_files = list()
    target_files.append(".env")
    target_files.append(DOCKER_COMPOSE_OVERRIDE_FILENAME)
    if not self._envs.has_server(self._server_name):
      return target_files
    for service in self._docker_compose_file.services:
      env_store = self._envs.server_store(self._server_name)
      if env_store.has_namespace(service):
        target_files.append(os.path.join(self._envs_folder, f"{service}.env"))
    return target_files

  def deploy_files(self, target_directory):
    """Generates a target server folder"""
    # docker-compose
    shutil.copyfile(
      self._docker_compose_file.filepath,
      os.path.join(target_directory, self._docker_compose_file.filename),
    )

    # .env file
    global_envs = dict()
    for namespace in self._envs.global_envs.namespaces:
      ns_envs = self._envs.global_envs.get_namespace_envs(namespace)
      global_envs.update(
        {
          f"{namespace.upper()}_{key.upper()}": value
          for key, value in ns_envs.items()
        }
      )
    write_dotenv(target_directory, global_envs)

    # docker-compose.override.yml
    override_file = EditableDockerCompose(version=None)  # version key is deprecated

    # envs/{SERVICE}.env files
    if self._envs.has_server(self._server_name):
      target_envs = os.path.join(target_directory, self._envs_folder)
      os.makedirs(target_envs)
      for service in self._docker_compose_file.services:
        env_store = self._envs.server_store(self._server_name)
        if not env_store.has_namespace(service):
          continue
        service_envs = env_store.get_namespace_envs(service)
        env_filepath = write_dotenv(target_envs, service_envs, filename=f"{service}.env")
        override_file.set_service_env_file(service, os.path.relpath(env_filepath, target_directory))

    # configs
    for service in self._docker_compose_file.services:
      src_service_configs_path = os.path.join(self._directory, self._configs_folder, service)
      if os.path.exists(src_service_configs_path):
        config_files = list_relative_files(src_service_configs_path)
        for config_file in sorted(config_files):
          source_file = os.path.join(self._configs_folder, service, config_file)
          target_file = os.path.join(self._configs_mount_point, config_file)
          override_file.add_service_volume(service, f"./{source_file}:{target_file}")

    src_config_dir = os.path.join(self._directory, self._configs_folder)
    if os.path.exists(src_config_dir):
      shutil.copytree(src_config_dir, os.path.join(target_directory, self._configs_folder),)

    # save override
    override_file.write_to(target_directory, DOCKER_COMPOSE_OVERRIDE_FILENAME)

    return target_directory

  def clean_generated_files(self, target_directory):
    for file_to_remove in self.generated_files:
      file_path = os.path.join(target_directory, file_to_remove)
      if not os.path.exists(file_path):
        continue
      os.remove(file_path)
      try:
        os.removedirs(os.path.dirname(file_path))
      except OSError:
        pass

    # double-check 'envs' folder
    envs_folder = os.path.join(self._directory, self._envs_folder)
    if os.path.isdir(envs_folder):
      os.rmdir(envs_folder)


class DeploymentFolder(Deployable):
  SERVER_DEFAULT = "default"

  def __init__(
    self,
    directory="/bootstrap",
    working_config_filename="cytomine.yml",
    template_config_filename="cytomine.template",
    configs_folder="configs",
    envs_folder="envs",
    ignored_dirs=None,
    configs_mount_point="/cm_configs",
    installer_config: InstallerConfig = None,
  ) -> None:
    """
    Parameters
    ----------
    directory: str
      Path of the directory where cytomine.yml is stored
    working_config_filename: str
      Name of the working config file
    template_config_filename: str
      Name of the template config file
    configs_folder: str
      Name of the configs folder in each server folder
    envs_folder: str
      Name of the target environment folder in the target server folder
    ignored_folders: set|list|NoneType
      Folders to ignore in the root directory
    configs_mount_point :
      Name of the configuration files mount path within the container
    installer_config: InstallerConfig
      Installer configuration, if not specified, default configuration is used
    """
    if ignored_dirs is None:
      ignored_dirs = set()

    if installer_config is None:
      installer_config = InstallerConfig()

    self._installer_config = installer_config
    self._directory = directory
    self._ignore_dirs = set(ignored_dirs)
    self._configs_folder = configs_folder
    self._envs_folder = envs_folder
    self._configs_mount_point = configs_mount_point
    self._working_config_filename = working_config_filename
    self._template_config_filename = template_config_filename
    self._working_config = ConfigFile(
      path=self._directory,
      filename=self._working_config_filename,
      file_must_exists=False,
    )
    self._template_config = ConfigFile(
      path=self._directory,
      filename=self._template_config_filename,
      file_must_exists=False,
    )

    if not os.path.isfile(self._working_config.filepath) and not os.path.isfile(self._template_config.filepath):
      raise FileNotFoundError(f"either {self._working_config.filepath} or {self._template_config.filepath} should exist, none found")

    # merge .template and .yml
    merge_policy = MergeEnvStorePolicy.PRESERVE
    if (
      installer_config.update_allow_list is not None
      and len(installer_config.update_allow_list) > 0
    ):
      merge_policy = MergeEnvStorePolicy.ALLOW_LIST

    self._merge_config = ConfigFile.merge(
      self._working_config,
      self._template_config,
      merge_policy=merge_policy,
      update_allow_list=installer_config.update_allow_list,
    )

    self._server_folders = dict()
    _, subdirs, subfiles = next(os.walk(self._directory))
    self._subdirs = set(subdirs).difference(self._ignore_dirs)

    ## checking server configuration (single or multi-server?)
    # we are in single-server mode if a docker-compose file is at the root
    self._single_server = DOCKER_COMPOSE_FILENAME in subfiles

    if not self._single_server:
      raise InvalidServerConfigurationError(f"cannot find {DOCKER_COMPOSE_FILENAME} at the"
                                            "root of the install folder")

    nb_servers_in_envs = len(self._merge_config.servers)
    if self._single_server and nb_servers_in_envs > 1:
      raise InvalidServerConfigurationError(
        f"it appears to be a single-server configuration ({DOCKER_COMPOSE_FILENAME} found"
        " in root directory) but several server entries have been found in cytomine.yml"
      )
    elif not self._single_server:
      envs_servers = set(self._merge_config.servers)
      folder_servers = self._subdirs
      if not envs_servers.issubset(folder_servers):
        raise InvalidServerConfigurationError(
          f"it appears to be a multi-server configuration ({DOCKER_COMPOSE_FILENAME} not"
          " found in root directory) but some server entries in cytomine.yml have no matching server folder"
        )

    server_folder_common_params = {
      "configs_folder": self._configs_folder,
      "envs_folder": self._envs_folder,
      "configs_mount_point": self._configs_mount_point,
    }

    if self._single_server:
      # single server
      self._server_folders[self.SERVER_DEFAULT] = ServerFolder(
        server_name=self.SERVER_DEFAULT,
        directory=self._directory,
        envs=self._merge_config,
        **server_folder_common_params,
      )
    else:
      for subdir in self._subdirs:
        self._server_folders[subdir] = ServerFolder(
          server_name=subdir,
          directory=os.path.join(self._directory, subdir),
          envs=self._merge_config,
          **server_folder_common_params,
        )

  @property
  def is_single_server(self):
    return self._single_server

  @property
  def server_folders(self):
    return self._server_folders

  def deploy_files(self, target_directory):
    # write config (cytomine.yml)
    dst_config_path = os.path.join(target_directory, self._merge_config.filename)
    with open(dst_config_path, "w", encoding="utf8") as file:
      yaml.dump(self._merge_config.export_dict(), file)

    # write template and config (if any in the base repository), copy file to avoid any change
    files_to_copy = [
      (self._template_config.filepath, self._template_config.filename),
      (self._installer_config.filepath, self._installer_config.filename),
    ]

    for source_filepath, target_filename in files_to_copy:
      if os.path.isfile(source_filepath):
        target_path = os.path.join(target_directory, target_filename)
        shutil.copyfile(source_filepath, target_path)

    # write server folders
    for server_folder in self._server_folders.values():
      if self._single_server:
        server_target_dir = target_directory
      else:
        server_target_dir = os.path.join(
          target_directory, server_folder.server_name
        )
        os.makedirs(server_target_dir)
      server_folder.deploy_files(server_target_dir)

  def clean_generated_files(self, target_directory):
    # clean target server folders to get back to a clean
    # deployable deployment folder
    for server_folder in self._server_folders.values():
      server_folder.clean_generated_files(target_directory)

  def _abs_to_relative(self, src_dir, files, ref_dir):
    return [os.path.relpath(os.path.join(src_dir, file), ref_dir) for file in files]

  @property
  def source_files(self):
    """List (existing) source files"""
    files = [self._working_config_filename]

    if os.path.isfile(self._template_config.filepath):
      files.append(self._template_config.filename)

    if os.path.isfile(self._installer_config.filepath):
      files.append(self._installer_config.filename)

    for server_folder in self._server_folders.values():
      files.extend(
        self._abs_to_relative(
          src_dir=server_folder.directory,
          files=server_folder.source_files,
          ref_dir=self._directory,
        )
      )

    return files

  @property
  def generated_files(self):
    files = list()
    for server_folder in self._server_folders.values():
      files.extend(
        self._abs_to_relative(
          src_dir=server_folder.directory,
          files=server_folder.generated_files,
          ref_dir=self._directory,
        )
      )
    return files
