use std::collections::BTreeMap;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::json;
use watasu::{
    CommandOptions, ConnectionConfig, ConnectionOptions, CreateOptions, CreateSnapshotOptions,
    Error, FileUrlOptions, GitAddOptions, GitCloneOptions, GitCommitOptions, GitConfigOptions,
    GitConfigureUserOptions, GitCredentialOptions, GitDeleteBranchOptions, GitInitOptions,
    GitRemoteAddOptions, GitRemoteOperationOptions, GitRequestOptions, GitResetOptions,
    GitRestoreOptions, NetworkUpdateOptions, PtyCreateOptions, PtySize, Sandbox, SandboxListQuery,
    SnapshotListOptions, Template, TemplateBuilder, Volume, VolumeCreateOptions, VolumeListOptions,
    VolumeMount, VolumeWriteOptions, WatchOptions, WriteEntry,
};

const REQUEST_TIMEOUT_SECS: u64 = 240;
const SANDBOX_TIMEOUT_SECS: u64 = 300;

#[tokio::test]
async fn live_broad_rust_sdk_smoke() -> watasu::Result<()> {
    if std::env::var("WATASU_LIVE_API_TESTS").ok().as_deref() != Some("1") {
        eprintln!("set WATASU_LIVE_API_TESTS=1 to run live SDK smoke tests");
        return Ok(());
    }

    let team = std::env::var("WATASU_SMOKE_TEAM").unwrap_or_else(|_| "watasu".to_string());
    let prefix = format!(
        "sdk-rs-{}-{}",
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_millis(),
        std::process::id()
    );

    assert!(std::env::var("WATASU_API_KEY").is_ok());
    assert!(ConnectionConfig::new(conn()).api_key.is_some());
    exercise_template_builder();
    assert!(Template::exists("base", conn()).await?);
    assert!(Template::alias_exists("base", conn()).await?);
    assert!(!Template::exists(format!("{prefix}-missing-template"), conn()).await?);
    let _tags = Template::get_tags("base", conn()).await?;

    let mut volume: Option<Volume> = None;
    let mut sandbox: Option<Sandbox> = None;
    let mut snapshots: Vec<String> = Vec::new();

    let result: watasu::Result<()> = async {
        let volume_name = format!("{prefix}-volume");
        volume = Some(
            Volume::create(
                &volume_name,
                VolumeCreateOptions {
                    connection: conn(),
                    team: Some(team.clone()),
                },
            )
            .await?,
        );
        let created_volume = volume.as_ref().unwrap();
        assert_eq!(
            Volume::get_info_by_id(&created_volume.volume_id, conn())
                .await?
                .name,
            volume_name
        );
        assert!(Volume::list(VolumeListOptions {
            connection: conn(),
            team: Some(team.clone()),
        })
        .await?
        .iter()
        .any(|item| item.volume_id == created_volume.volume_id));
        let connected_volume = Volume::connect(&created_volume.volume_id, conn()).await?;
        connected_volume
            .make_dir(
                "/workspace",
                VolumeWriteOptions {
                    force: Some(true),
                    ..VolumeWriteOptions::default()
                },
            )
            .await?;
        connected_volume
            .write_file(
                "/workspace/hello.txt",
                b"volume-ok",
                VolumeWriteOptions {
                    force: Some(true),
                    ..VolumeWriteOptions::default()
                },
            )
            .await?;
        assert_eq!(
            connected_volume.read_file("/workspace/hello.txt").await?,
            b"volume-ok"
        );
        assert!(connected_volume.exists("/workspace/hello.txt").await?);
        assert!(!connected_volume.exists("/workspace/missing.txt").await?);
        assert_eq!(
            connected_volume
                .get_path_info("/workspace/hello.txt")
                .await?
                .entry_type,
            "file"
        );
        assert!(connected_volume
            .list_files("/workspace", Some(1))
            .await?
            .iter()
            .any(|entry| entry.name == "hello.txt"));
        connected_volume
            .update_metadata(
                "/workspace/hello.txt",
                VolumeWriteOptions {
                    mode: Some("0644".to_string()),
                    ..VolumeWriteOptions::default()
                },
            )
            .await?;
        assert!(connected_volume.remove("/workspace/hello.txt").await?);

        sandbox = Some(
            Sandbox::create(create_opts(
                "base",
                &team,
                &prefix,
                "rust",
                &[VolumeMount::new(
                    "/mnt/smoke-volume",
                    created_volume.name.clone(),
                )],
            ))
            .await?,
        );
        let sbx = sandbox.as_mut().unwrap();
        assert!(!sbx.sandbox_id.is_empty());
        assert_eq!(sbx.get_info().await?.sandbox_id, sbx.sandbox_id);
        sbx.set_timeout(SANDBOX_TIMEOUT_SECS).await?;
        let mut query = SandboxListQuery::default();
        query.metadata.insert("smoke".into(), json!(prefix));
        assert!(Sandbox::list(watasu::ListOptions {
            connection: conn(),
            query: Some(query),
            limit: Some(10),
            team: Some(team.clone()),
            ..watasu::ListOptions::default()
        })
        .await?
        .sandboxes
        .iter()
        .any(|item| item.sandbox_id == sbx.sandbox_id));
        assert!(sbx.get_host(8080).await?.contains("sandbox."));
        assert!(sbx.get_mcp_url().await?.contains("/mcp"));
        assert!(sbx.get_mcp_token().await?.is_none());
        let _metrics = sbx.get_metrics().await?;
        let _metrics_by_id = Sandbox::get_metrics_by_id(&sbx.sandbox_id, conn()).await?;
        sbx.update_network(NetworkUpdateOptions {
            allow_internet_access: Some(true),
            ..NetworkUpdateOptions::default()
        })
        .await?;
        Sandbox::update_network_by_id(
            &sbx.sandbox_id,
            NetworkUpdateOptions {
                allow_internet_access: Some(true),
                ..NetworkUpdateOptions::default()
            },
            conn(),
        )
        .await?;

        exercise_files(sbx, &prefix).await?;
        exercise_signed_file_urls(sbx, &prefix).await?;
        exercise_commands(sbx).await?;
        exercise_pty(sbx).await?;
        exercise_git(sbx, &prefix).await?;

        let snapshot = sbx
            .create_snapshot(CreateSnapshotOptions {
                name: Some(format!("{prefix}-snapshot")),
                ..CreateSnapshotOptions::default()
            })
            .await?;
        snapshots.push(snapshot.snapshot_id.clone());
        assert!(!snapshot.snapshot_id.is_empty());
        assert!(sbx
            .list_snapshots()
            .await?
            .iter()
            .any(|item| item.snapshot_id == snapshot.snapshot_id));
        assert!(Sandbox::list_snapshots_page(SnapshotListOptions {
            connection: conn(),
            sandbox_id: Some(sbx.sandbox_id.clone()),
            limit: Some(10),
            ..SnapshotListOptions::default()
        })
        .await?
        .snapshots
        .iter()
        .any(|item| item.snapshot_id == snapshot.snapshot_id));
        assert!(sbx.delete_snapshot(&snapshot.snapshot_id).await?);
        snapshots.pop();
        assert!(
            !Sandbox::delete_snapshot_by_id(format!("{prefix}-missing-snapshot"), conn()).await?
        );

        let mut connected = Sandbox::connect(&sbx.sandbox_id, conn()).await?;
        assert_eq!(connected.sandbox_id, sbx.sandbox_id);
        assert_eq!(
            connected.commands.run("printf connected-ok").await?.stdout,
            "connected-ok"
        );
        assert!(connected.resume().await?);

        Ok(())
    }
    .await;

    for snapshot_id in snapshots {
        let _ = Sandbox::delete_snapshot_by_id(snapshot_id, conn()).await;
    }
    if let Some(sbx) = sandbox.as_ref() {
        let _ = sbx.kill().await;
    }
    if let Some(volume) = volume.as_ref() {
        let _ = volume.destroy().await;
    }

    result?;
    assert!(!Volume::destroy_by_id(format!("{prefix}-missing-volume"), conn()).await?);
    Ok(())
}

async fn exercise_files(sbx: &Sandbox, prefix: &str) -> watasu::Result<()> {
    let dir = format!("/tmp/{prefix}-files");
    sbx.files.make_dir(&dir).await?;
    let mut watcher = sbx
        .files
        .watch_dir(
            &dir,
            WatchOptions {
                include_entry: true,
                ..WatchOptions::default()
            },
        )
        .await?;
    assert!(!sbx.files.exists(&format!("{dir}/missing.txt")).await?);
    sbx.files
        .write(&format!("{dir}/hello.txt"), b"file-ok")
        .await?;
    sbx.files
        .write(&format!("{dir}/bytes.bin"), [4, 5, 6])
        .await?;
    sbx.files
        .write_files(vec![
            WriteEntry::new(format!("{dir}/batch-a.txt"), b"a"),
            WriteEntry::new(format!("{dir}/batch-b.txt"), b"b"),
        ])
        .await?;
    assert_eq!(
        sbx.files.read_text(&format!("{dir}/hello.txt")).await?,
        "file-ok"
    );
    assert_eq!(
        sbx.files.read_bytes(&format!("{dir}/bytes.bin")).await?,
        vec![4, 5, 6]
    );
    assert_eq!(
        sbx.files.get_info(&format!("{dir}/hello.txt")).await?.name,
        "hello.txt"
    );
    assert!(sbx
        .files
        .list(&dir)
        .await?
        .iter()
        .any(|entry| entry.name == "hello.txt"));
    assert!(sbx.files.exists(&format!("{dir}/hello.txt")).await?);
    let renamed = sbx
        .files
        .rename(&format!("{dir}/hello.txt"), &format!("{dir}/renamed.txt"))
        .await?;
    assert_eq!(renamed.name, "renamed.txt");
    let events = tokio::time::timeout(Duration::from_secs(10), watcher.next_events())
        .await
        .map_err(|_| Error::Timeout)??;
    assert!(events
        .unwrap_or_default()
        .iter()
        .any(|event| !event.path.is_empty()));
    let _ = watcher.stop().await;
    sbx.files.remove(&format!("{dir}/renamed.txt")).await?;
    Ok(())
}

async fn exercise_signed_file_urls(sbx: &Sandbox, prefix: &str) -> watasu::Result<()> {
    let path = format!("/tmp/{prefix}-signed.txt");
    let upload = sbx
        .upload_url_info(
            &path,
            FileUrlOptions {
                expires_in_seconds: Some(120),
                ..FileUrlOptions::default()
            },
        )
        .await?;
    assert_eq!(upload.method, "POST");
    assert_eq!(upload.path, path);
    request_bytes(&upload.url, &upload.method, Some(b"signed-ok".to_vec())).await?;
    let download = sbx
        .download_url_info(
            &path,
            FileUrlOptions {
                expires_in_seconds: Some(120),
                ..FileUrlOptions::default()
            },
        )
        .await?;
    assert_eq!(download.method, "GET");
    assert!(sbx
        .upload_url(&path, FileUrlOptions::default())
        .await?
        .starts_with("http"));
    assert!(sbx
        .download_url(&path, FileUrlOptions::default())
        .await?
        .starts_with("http"));
    assert_eq!(
        request_bytes(&download.url, &download.method, None).await?,
        b"signed-ok"
    );
    Ok(())
}

async fn exercise_commands(sbx: &Sandbox) -> watasu::Result<()> {
    assert_eq!(
        sbx.commands.run("printf command-ok").await?.stdout,
        "command-ok"
    );
    let error = sbx.commands.run("echo fail >&2; exit 7").await.unwrap_err();
    assert!(matches!(
        error,
        Error::CommandExit { result } if result.exit_code == 7
    ));

    let mut cat = sbx
        .commands
        .run_background_with_options(
            "cat",
            CommandOptions {
                stdin: true,
                timeout_ms: Some(30_000),
            },
        )
        .await?;
    cat.send_stdin("stdin-ok\n").await?;
    cat.close_stdin().await?;
    assert_eq!(cat.wait().await?.stdout, "stdin-ok\n");

    let mut sleeper = sbx.commands.run_background("sleep 60").await?;
    assert!(sbx
        .commands
        .list()
        .await?
        .iter()
        .any(|item| item.pid == sleeper.pid));
    let mut attached = sbx.commands.connect(&sleeper.pid).await?;
    attached.disconnect().await?;
    assert!(sleeper.kill().await?);
    let _ = sleeper.wait().await;
    Ok(())
}

async fn exercise_pty(sbx: &Sandbox) -> watasu::Result<()> {
    let mut handle = sbx
        .pty
        .create(PtyCreateOptions {
            timeout_ms: Some(30_000),
            ..PtyCreateOptions::default()
        })
        .await?;
    handle.send_stdin("printf pty-ok; exit\n").await?;
    assert!(handle.wait().await?.stdout.contains("pty-ok"));

    let mut long = sbx
        .pty
        .create(PtyCreateOptions {
            timeout_ms: Some(120_000),
            ..PtyCreateOptions::default()
        })
        .await?;
    let mut connected = sbx.pty.connect(&long.pid).await?;
    connected.disconnect().await?;
    sbx.pty
        .resize(
            &long.pid,
            PtySize {
                cols: 100,
                rows: 30,
            },
        )
        .await?;
    let _ = sbx.pty.send_input(&long.pid, "echo ignored\n").await;
    assert!(sbx.pty.kill(&long.pid).await?);
    let _ = long.wait().await;
    Ok(())
}

async fn exercise_git(sbx: &Sandbox, prefix: &str) -> watasu::Result<()> {
    let repo = format!("/tmp/{prefix}-repo");
    let remote = format!("/tmp/{prefix}-remote.git");
    let clone = format!("/tmp/{prefix}-clone");
    sbx.commands
        .run(&format!("rm -rf {repo} {remote} {clone}"))
        .await?;
    sbx.commands
        .run(&format!("git init --bare {remote}"))
        .await?;
    sbx.git
        .dangerously_authenticate(GitCredentialOptions {
            username: "user".to_string(),
            password: "token".to_string(),
            host: Some("example.test".to_string()),
            protocol: Some("https".to_string()),
            ..GitCredentialOptions::default()
        })
        .await?;
    sbx.git
        .init(
            &repo,
            GitInitOptions {
                initial_branch: Some("main".to_string()),
                ..GitInitOptions::default()
            },
        )
        .await?;
    sbx.git
        .configure_user(
            "Watasu Smoke",
            "smoke@watasu.io",
            GitConfigureUserOptions {
                path: Some(repo.clone()),
                scope: Some("local".to_string()),
                ..GitConfigureUserOptions::default()
            },
        )
        .await?;
    sbx.git
        .set_config(
            "smoke.key",
            "smoke-value",
            GitConfigOptions {
                path: Some(repo.clone()),
                scope: Some("local".to_string()),
                ..GitConfigOptions::default()
            },
        )
        .await?;
    assert_eq!(
        sbx.git
            .get_config(
                "smoke.key",
                GitConfigOptions {
                    path: Some(repo.clone()),
                    scope: Some("local".to_string()),
                    ..GitConfigOptions::default()
                },
            )
            .await?,
        "smoke-value"
    );
    sbx.commands
        .run(&format!("printf one > {repo}/file.txt"))
        .await?;
    assert!(sbx
        .git
        .status(&repo, GitRequestOptions::default())
        .await?
        .has_untracked());
    sbx.git
        .add(
            &repo,
            GitAddOptions {
                files: vec!["file.txt".to_string()],
                ..GitAddOptions::default()
            },
        )
        .await?;
    sbx.git
        .commit(&repo, "initial", GitCommitOptions::default())
        .await?;
    assert!(sbx
        .git
        .status(&repo, GitRequestOptions::default())
        .await?
        .is_clean());
    sbx.git
        .create_branch(&repo, "feature", GitRequestOptions::default())
        .await?;
    assert_eq!(
        sbx.git
            .branches(&repo, GitRequestOptions::default())
            .await?
            .current_branch
            .as_deref(),
        Some("feature")
    );
    sbx.git
        .checkout_branch(&repo, "main", GitRequestOptions::default())
        .await?;
    sbx.git
        .delete_branch(
            &repo,
            "feature",
            GitDeleteBranchOptions {
                force: true,
                ..GitDeleteBranchOptions::default()
            },
        )
        .await?;
    sbx.git
        .remote_add(
            &repo,
            "origin",
            &remote,
            GitRemoteAddOptions {
                overwrite: true,
                ..GitRemoteAddOptions::default()
            },
        )
        .await?;
    assert_eq!(
        sbx.git
            .remote_get(&repo, "origin", GitRequestOptions::default())
            .await?
            .as_deref(),
        Some(remote.as_str())
    );
    sbx.git
        .push(
            &repo,
            GitRemoteOperationOptions {
                remote: Some("origin".to_string()),
                branch: Some("main".to_string()),
                set_upstream: true,
                ..GitRemoteOperationOptions::default()
            },
        )
        .await?;
    sbx.git
        .clone(
            &remote,
            GitCloneOptions {
                path: Some(clone.clone()),
                branch: Some("main".to_string()),
                ..GitCloneOptions::default()
            },
        )
        .await?;
    sbx.commands
        .run(&format!("printf two > {repo}/file.txt"))
        .await?;
    sbx.git.add(&repo, GitAddOptions::default()).await?;
    sbx.git
        .commit(&repo, "second", GitCommitOptions::default())
        .await?;
    sbx.git
        .push(
            &repo,
            GitRemoteOperationOptions {
                remote: Some("origin".to_string()),
                branch: Some("main".to_string()),
                set_upstream: true,
                ..GitRemoteOperationOptions::default()
            },
        )
        .await?;
    sbx.git
        .pull(
            &clone,
            GitRemoteOperationOptions {
                remote: Some("origin".to_string()),
                branch: Some("main".to_string()),
                ..GitRemoteOperationOptions::default()
            },
        )
        .await?;
    assert_eq!(
        sbx.files.read_text(&format!("{clone}/file.txt")).await?,
        "two"
    );
    sbx.commands
        .run(&format!("printf dirty > {repo}/file.txt"))
        .await?;
    sbx.git
        .restore(
            &repo,
            GitRestoreOptions {
                paths: vec!["file.txt".to_string()],
                worktree: Some(true),
                ..GitRestoreOptions::default()
            },
        )
        .await?;
    assert_eq!(
        sbx.files.read_text(&format!("{repo}/file.txt")).await?,
        "two"
    );
    sbx.commands
        .run(&format!("printf staged > {repo}/staged.txt"))
        .await?;
    sbx.git
        .add(
            &repo,
            GitAddOptions {
                files: vec!["staged.txt".to_string()],
                ..GitAddOptions::default()
            },
        )
        .await?;
    sbx.git
        .reset(
            &repo,
            GitResetOptions {
                paths: vec!["staged.txt".to_string()],
                ..GitResetOptions::default()
            },
        )
        .await?;
    assert!(sbx
        .git
        .status(&repo, GitRequestOptions::default())
        .await?
        .has_untracked());
    sbx.git
        .checkout(&repo, "main", GitRequestOptions::default())
        .await?;
    Ok(())
}

fn exercise_template_builder() {
    let mut env = BTreeMap::new();
    env.insert("WATASU_SMOKE".to_string(), "1".to_string());
    let builder = TemplateBuilder::new()
        .from_base_image()
        .from_debian_image("stable")
        .from_ubuntu_image("latest")
        .from_python_image("3")
        .from_node_image("lts")
        .from_bun_image("latest")
        .from_image("ignored")
        .from_template("base")
        .apt_install(["git"])
        .pip_install(["pytest"])
        .npm_install(["typescript"])
        .set_workdir("/workspace")
        .set_user("root")
        .set_envs(env)
        .set_start_cmd("sleep infinity", "true")
        .add_mcp_server(["server-one"])
        .run_cmd("true")
        .skip_cache();
    assert_eq!(builder.build_spec()["from_template"], "base");
    assert!(builder.to_json().contains("\"packages\""));
    assert!(builder.to_dockerfile().contains("FROM base"));
}

fn create_opts(
    template: &str,
    team: &str,
    prefix: &str,
    sdk: &str,
    volume_mounts: &[VolumeMount],
) -> CreateOptions {
    let mut metadata = serde_json::Map::new();
    metadata.insert("smoke".into(), json!(prefix));
    metadata.insert("sdk".into(), json!(sdk));
    let mut envs = serde_json::Map::new();
    envs.insert("WATASU_SMOKE_VALUE".into(), json!("env-ok"));
    CreateOptions {
        connection: conn(),
        template: template.to_string(),
        timeout_seconds: SANDBOX_TIMEOUT_SECS,
        metadata,
        envs,
        team: Some(team.to_string()),
        volume_mounts: volume_mounts.to_vec(),
        ..CreateOptions::default()
    }
}

fn conn() -> ConnectionOptions {
    ConnectionOptions {
        request_timeout_secs: Some(REQUEST_TIMEOUT_SECS),
        ..ConnectionOptions::default()
    }
}

async fn request_bytes(url: &str, method: &str, body: Option<Vec<u8>>) -> watasu::Result<Vec<u8>> {
    let method = reqwest::Method::from_bytes(method.as_bytes())
        .map_err(|error| Error::Sandbox(error.to_string()))?;
    let client = reqwest::Client::new();
    let mut request = client.request(method, url);
    if let Some(body) = body {
        request = request.body(body);
    }
    let response = request.send().await?.error_for_status()?;
    Ok(response.bytes().await?.to_vec())
}
