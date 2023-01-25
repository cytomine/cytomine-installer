import os
import shutil
import yaml
import pathlib
from tempfile import TemporaryDirectory
from unittest import TestCase
from bootstrapper.deployment.deployment_files import CytomineEnvsFile
from bootstrapper.deployment.deployment_folders import DeploymentFolder, InvalidServerConfigurationError, ServerFolder
from bootstrapper.deployment.errors import MissingCytomineYamlFileError, NoDockerComposeYamlFileError
from bootstrapper.util import list_relative_files



def parse_yaml(path, filename):
  with open(os.path.join(path, filename), "r", encoding="utf8") as file:
    return yaml.load(file, Loader=yaml.Loader)


def parse_dotenv(path):
  with open(path, "r", encoding="utf8") as file:
    return {line.split("=", 1)[0]: line.strip().split("=", 1)[1] for line in file.readlines()}


class FileSystemTestCase(TestCase):
  def assertIsFile(self, path):
      if not pathlib.Path(path).resolve().is_file():
          raise AssertionError(f"file does not exist: {path}")

  def assertSameTextFileContent(self, path1, path2):
    self.assertIsFile(path1)
    self.assertIsFile(path2)
    with open(path1, "r", encoding="utf8") as file1, open(path2, "r", encoding="utf8") as file2:
      self.assertEqual(file1.read(), file2.read())

  def assertSameYamlFileContent(self, path1, path2):
    self.assertIsFile(path1)
    self.assertIsFile(path2)
    with open(path1, "r", encoding="utf8") as file1, open(path2, "r", encoding="utf8") as file2:
      yml1 = yaml.load(file1, Loader=yaml.Loader)
      yml2 = yaml.load(file2, Loader=yaml.Loader)
      self.assertDictEqual(yml1, yml2)

  def assertSameDotenvFileContent(self, path1, path2):
    self.assertIsFile(path1)
    self.assertIsFile(path2)
    dotenv1 = parse_dotenv(path1)
    dotenv2 = parse_dotenv(path2)
    self.assertDictEqual(dotenv1, dotenv2)

  def assertSameDirectories(self, gen_path, ref_path):
    ref_rel_files = list_relative_files(ref_path)
    for out_rel_file in ref_rel_files:
      ref_filepath = os.path.join(ref_path, out_rel_file)
      gen_filepath = os.path.join(gen_path, out_rel_file)
      self.assertIsFile(gen_filepath)

      if out_rel_file.endswith("yml"):
        ### Check *.yml files
        self.assertSameYamlFileContent(gen_filepath, ref_filepath)
      elif out_rel_file.endswith(".env"):
        self.assertSameDotenvFileContent(gen_filepath, ref_filepath)
      else:
        self.assertSameTextFileContent(gen_filepath, ref_filepath)



class TestServerFolder(FileSystemTestCase):
  def testListSourceFiles(self):
    tests_path = os.path.dirname(__file__)
    deploy_path = os.path.join(tests_path, "files", "fake_single_server", "in")
    envs_file = CytomineEnvsFile(deploy_path)
    server_folder = ServerFolder("default", deploy_path, envs_file)
    self.assertSetEqual(set(server_folder.source_files), {
      "configs/core/etc/cytomine/cytomine-app.yml",
      "configs/ims/usr/local/cytom/ims.conf",
      "docker-compose.yml"
    })

  def testGeneratedFiles(self):
    tests_path = os.path.dirname(__file__)
    deploy_path = os.path.join(tests_path, "files", "fake_single_server", "in")
    envs_file = CytomineEnvsFile(deploy_path)
    server_folder = ServerFolder("default", deploy_path, envs_file)
    self.assertSetEqual(set(server_folder.generated_files), {
      "envs/core.env",
      "envs/ims.env",
      ".env",
      "docker-compose.override.yml"
    })

  def testTargetFiles(self):
    tests_path = os.path.dirname(__file__)
    deploy_path = os.path.join(tests_path, "files", "fake_single_server", "in")
    envs_file = CytomineEnvsFile(deploy_path)
    server_folder = ServerFolder("default", deploy_path, envs_file)
    self.assertSetEqual(set(server_folder.target_files), set(server_folder.source_files).union(server_folder.generated_files))

  def testFilesFunctionsOneServiceWithoutEnvs(self):
    tests_path = os.path.dirname(__file__)
    deploy_path = os.path.join(tests_path, "files", "fake_multi_server", "in")
    server_path = os.path.join(deploy_path, "server-core")
    envs_file = CytomineEnvsFile(deploy_path)
    server_folder = ServerFolder("server-core", server_path, envs_file)
    self.assertSetEqual(set(server_folder.source_files), {
      "configs/core/etc/cytomine/cytomine-app.yml",
      "docker-compose.yml"
    })
    self.assertSetEqual(set(server_folder.generated_files), {
      "envs/core.env",
      "envs/postgres.env",
      ".env",
      "docker-compose.override.yml"
    })
    
  def testCleanValid(self):
    tests_path = os.path.dirname(__file__)
    deploy_path = os.path.join(tests_path, "files", "fake_multi_server", "in")
    server_path = os.path.join(deploy_path, "server-core")
    out_deploy_path = os.path.join(tests_path, "files", "fake_multi_server", "out")
    out_server_path = os.path.join(out_deploy_path, "server-core")
    envs_file = CytomineEnvsFile(deploy_path)
    server_folder = ServerFolder("server-core", server_path, envs_file)
    with TemporaryDirectory() as tmpdir:
      target_server_path = os.path.join(tmpdir, "out")
      shutil.copytree(out_server_path, target_server_path)
      self.assertSetEqual(
        set(list_relative_files(target_server_path)), 
        set(server_folder.target_files)
      )
      server_folder.clean_generated_files(target_server_path)
      self.assertSetEqual(
        set(list_relative_files(target_server_path)), 
        set(server_folder.source_files)
      )



class TestDeploymentFolder(FileSystemTestCase):
  def testSingleServerDeployment(self):
    tests_path = os.path.dirname(__file__)
    deploy_path = os.path.join(tests_path, "files", "fake_single_server", "in")
    output_ref_path = os.path.join(tests_path, "files", "fake_single_server", "out") 
    deployment_folder = DeploymentFolder(directory=deploy_path)
    with TemporaryDirectory() as tmpdir:
      deployment_folder.deploy_files(tmpdir)
      out_rel_files = list_relative_files(output_ref_path)

      for out_rel_file in out_rel_files:
        reference_filepath = os.path.join(output_ref_path, out_rel_file)
        generated_filepath = os.path.join(tmpdir, out_rel_file)
        self.assertIsFile(generated_filepath)

        if os.path.basename(out_rel_file) == "cytomine.yml":
          ### Check Cytomine.yml file
          generated_content = parse_yaml(os.path.dirname(generated_filepath), "cytomine.yml")
          reference_content = parse_yaml(os.path.dirname(reference_filepath), "cytomine.yml")

          # need to get the autogenerated field from the generated yaml
          # but first need to check if this field exists in the generated yaml
          autogenerated_key_path = ["services", "default", "ims", "constant", "IMS_VAR1"]
          resolved = generated_content
          for key in autogenerated_key_path:
            self.assertIn(key, resolved)
            resolved = resolved[key]

          reference_content["services"]["default"]["ims"]["constant"]["IMS_VAR1"] = resolved

          self.assertDictEqual(generated_content, reference_content)
        elif out_rel_file.endswith("yml"):
          ### Check other *.yml files
          self.assertSameYamlFileContent(generated_filepath, reference_filepath)
        elif out_rel_file.endswith(".env") and "ims" in os.path.basename(out_rel_file):
          ### Check service ims.env files 
          # need to replace the auto generated value !!
          reference_dotenv = parse_dotenv(reference_filepath)
          generated_dotenv = parse_dotenv(generated_filepath)
          self.assertIn("IMS_VAR1", generated_dotenv)
          reference_dotenv["IMS_VAR1"] = generated_dotenv["IMS_VAR1"]
          self.assertDictEqual(generated_dotenv, reference_dotenv)
        elif out_rel_file.endswith(".env"):
          ### Check .env file
          self.assertSameDotenvFileContent(generated_filepath, reference_filepath)
        else: # check configuration files
          self.assertSameTextFileContent(generated_filepath, reference_filepath)

  def testMultiServerConfiguration(self):
    tests_path = os.path.dirname(__file__)
    deploy_path = os.path.join(tests_path, "files", "fake_multi_server", "in")
    output_ref_path = os.path.join(tests_path, "files", "fake_multi_server", "out") 
    deployment_folder = DeploymentFolder(directory=deploy_path)
    with TemporaryDirectory() as tmpdir:
      deployment_folder.deploy_files(tmpdir)
      out_rel_files = list_relative_files(output_ref_path)

      for out_rel_file in out_rel_files:
        reference_filepath = os.path.join(output_ref_path, out_rel_file)
        generated_filepath = os.path.join(tmpdir, out_rel_file)
        self.assertIsFile(generated_filepath)

        if out_rel_file.endswith("yml"):
          ### Check *.yml files
          self.assertSameYamlFileContent(generated_filepath, reference_filepath)
        elif out_rel_file.endswith(".env"):
          self.assertSameDotenvFileContent(generated_filepath, reference_filepath)
        else:
          self.assertSameTextFileContent(generated_filepath, reference_filepath)

  def testMultiServerMissingServerFolder(self):
    tests_path = os.path.dirname(__file__)
    deploy_path = os.path.join(tests_path, "files", "fake_multi_server_missing_folder")
    with self.assertRaises(InvalidServerConfigurationError):
      DeploymentFolder(directory=deploy_path)

  def testNoCytomineYml(self):
    tests_path = os.path.dirname(__file__)
    deploy_path = os.path.join(tests_path, "files", "fake_no_cytomine_yml")
    with self.assertRaises(MissingCytomineYamlFileError):
      DeploymentFolder(directory=deploy_path)

  def testNoDockerComposeFile(self):
    tests_path = os.path.dirname(__file__)
    deploy_path = os.path.join(tests_path, "files", "fake_no_docker_compose_yml")
    with self.assertRaises(InvalidServerConfigurationError):
      DeploymentFolder(directory=deploy_path)
