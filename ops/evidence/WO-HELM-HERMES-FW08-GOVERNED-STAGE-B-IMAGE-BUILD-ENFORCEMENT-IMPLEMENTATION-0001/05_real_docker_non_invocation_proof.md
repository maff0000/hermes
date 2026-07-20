# Real-Docker Non-Invocation Proof

Mechanisms (all tested in tests/test_fw08_governed_build_v1.py):
1. **Default runner refuses** — `test_default_runner_refuses_build`: `RefusingDockerRunner().build(...)`
   raises `RealDockerInvocationForbidden`; the wrapper with no injected runner uses it and rejects.
2. **Real runner doubly gated, fails before subprocess** — `test_real_runner_refuses_without_double_gate`:
   `RealDockerRunner()` and `RealDockerRunner(enable_real_execution=True)` (env unset) both raise BEFORE any
   subprocess; a monkeypatched `subprocess.run` that would flag a `docker` argv is never called (count==0).
3. **Full flow never spawns docker** — `test_full_flow_never_invokes_real_docker`: `subprocess.run` AND
   `subprocess.Popen` are monkeypatched to assert argv[0] never contains `docker`; the governed flow reaches
   CANDIDATE_READY via the injected FakeRunner (git subprocess for archive/rev-parse is allowed; no docker).
4. **Static** — `test_exact_docker_command_array_and_no_shell` + `test_no_publish_no_deploy_static_guards`:
   no `shell=True`/`os.system`/`os.popen`/`eval`/`exec`/`pickle`/`docker push`/`docker run`/`kubectl`/`helm
   install`/redis/sql in the wrapper source. The only real `subprocess.run([...docker...])` lives inside
   `RealDockerRunner.build`, after the double gate.
