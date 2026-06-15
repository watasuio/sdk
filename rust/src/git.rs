use serde_json::Value;

use crate::error::Result;
use crate::transport::DataPlaneClient;

/// Result returned by a sandbox Git command.
#[derive(Clone, Debug, Default)]
pub struct GitCommandResult {
    /// Repository path used by the command.
    pub path: Option<String>,
    /// Git URL used by clone operations.
    pub url: Option<String>,
    /// Ref used by checkout operations.
    pub ref_name: Option<String>,
    /// Branch used by branch operations.
    pub branch: Option<String>,
    /// Remote used by pull/push operations.
    pub remote: Option<String>,
    /// Name returned by remote operations.
    pub name: Option<String>,
    /// Config value returned by get_config.
    pub value: Option<String>,
    /// Branch names returned by branches.
    pub branches: Vec<String>,
    /// Current branch returned by branches.
    pub current_branch: Option<String>,
    /// Captured stdout.
    pub stdout: String,
    /// Captured stderr.
    pub stderr: String,
    /// Runtime command metadata.
    pub command: Option<Value>,
    /// Full raw Git payload.
    pub raw: Value,
}

/// Parsed status entry for one repository file.
#[derive(Clone, Debug, Default)]
pub struct GitFileStatus {
    /// Path relative to the repository root.
    pub name: String,
    /// Normalized status name.
    pub status: String,
    /// Index status character.
    pub index_status: String,
    /// Working tree status character.
    pub working_tree_status: String,
    /// Whether this change is staged.
    pub staged: bool,
    /// Original path for rename entries.
    pub renamed_from: Option<String>,
}

/// Parsed repository status.
#[derive(Clone, Debug, Default)]
pub struct GitStatus {
    /// Current branch, when known.
    pub current_branch: Option<String>,
    /// Upstream branch, when known.
    pub upstream: Option<String>,
    /// Number of commits ahead of upstream.
    pub ahead: u64,
    /// Number of commits behind upstream.
    pub behind: u64,
    /// Whether HEAD is detached.
    pub detached: bool,
    /// File status entries.
    pub file_status: Vec<GitFileStatus>,
    /// Raw command result used to build this status.
    pub result: GitCommandResult,
}

/// Branch list returned by `Git::branches`.
#[derive(Clone, Debug, Default)]
pub struct GitBranches {
    /// Repository path used by the command.
    pub path: Option<String>,
    /// Branch names.
    pub branches: Vec<String>,
    /// Current branch, when known.
    pub current_branch: Option<String>,
    /// Raw command result.
    pub result: GitCommandResult,
}

impl GitStatus {
    /// Return true when there are no tracked or untracked changes.
    pub fn is_clean(&self) -> bool {
        self.file_status.is_empty()
    }

    /// Return true when the repository has any changed files.
    pub fn has_changes(&self) -> bool {
        !self.file_status.is_empty()
    }

    /// Return true when at least one file has staged changes.
    pub fn has_staged(&self) -> bool {
        self.file_status.iter().any(|item| item.staged)
    }

    /// Return true when at least one file is untracked.
    pub fn has_untracked(&self) -> bool {
        self.file_status
            .iter()
            .any(|item| item.status == "untracked")
    }

    /// Return the total number of changed files.
    pub fn total_count(&self) -> usize {
        self.file_status.len()
    }
}

/// Options for Git clone.
#[derive(Clone, Debug, Default)]
pub struct GitCloneOptions {
    /// Destination path.
    pub path: Option<String>,
    /// Branch to clone.
    pub branch: Option<String>,
    /// Shallow clone depth.
    pub depth: Option<u64>,
    /// Whether to recurse into submodules.
    pub recursive: bool,
    /// Alias for `recursive`.
    pub submodules: bool,
    /// Optional username for private HTTP(S) repositories.
    pub username: Option<String>,
    /// Optional password or token for private HTTP(S) repositories.
    pub password: Option<String>,
    /// Keep credentials in the clone remote URL.
    pub dangerously_store_credentials: bool,
    /// Environment variables for the Git process.
    pub envs: serde_json::Map<String, Value>,
    /// Server-side timeout in seconds.
    pub timeout_seconds: Option<u64>,
}

/// Options shared by Git repository operations.
#[derive(Clone, Debug, Default)]
pub struct GitRequestOptions {
    /// Environment variables for the Git process.
    pub envs: serde_json::Map<String, Value>,
    /// Server-side timeout in seconds.
    pub timeout_seconds: Option<u64>,
}

/// Options for initializing a Git repository.
#[derive(Clone, Debug, Default)]
pub struct GitInitOptions {
    /// Initialize a bare repository.
    pub bare: bool,
    /// Initial branch name.
    pub initial_branch: Option<String>,
    /// Environment variables for the Git process.
    pub envs: serde_json::Map<String, Value>,
    /// Server-side timeout in seconds.
    pub timeout_seconds: Option<u64>,
}

/// Options for Git credentials stored in the sandbox credential helper.
#[derive(Clone, Debug, Default)]
pub struct GitCredentialOptions {
    /// Username to store.
    pub username: String,
    /// Password or token to store.
    pub password: String,
    /// Git host. Defaults server-side when omitted.
    pub host: Option<String>,
    /// Git protocol. Defaults server-side when omitted.
    pub protocol: Option<String>,
    /// Environment variables for the Git process.
    pub envs: serde_json::Map<String, Value>,
    /// Server-side timeout in seconds.
    pub timeout_seconds: Option<u64>,
}

/// Options for configuring Git user identity.
#[derive(Clone, Debug, Default)]
pub struct GitConfigureUserOptions {
    /// Config scope: `global` or `local`.
    pub scope: Option<String>,
    /// Repository path for local scope.
    pub path: Option<String>,
    /// Environment variables for the Git process.
    pub envs: serde_json::Map<String, Value>,
    /// Server-side timeout in seconds.
    pub timeout_seconds: Option<u64>,
}

/// Options for deleting a Git branch.
#[derive(Clone, Debug, Default)]
pub struct GitDeleteBranchOptions {
    /// Force branch deletion.
    pub force: bool,
    /// Environment variables for the Git process.
    pub envs: serde_json::Map<String, Value>,
    /// Server-side timeout in seconds.
    pub timeout_seconds: Option<u64>,
}

/// Options for staging files.
#[derive(Clone, Debug, Default)]
pub struct GitAddOptions {
    /// File paths to stage. Empty means all files.
    pub files: Vec<String>,
    /// Environment variables for the Git process.
    pub envs: serde_json::Map<String, Value>,
    /// Server-side timeout in seconds.
    pub timeout_seconds: Option<u64>,
}

/// Options for committing files.
#[derive(Clone, Debug, Default)]
pub struct GitCommitOptions {
    /// Author name override.
    pub author_name: Option<String>,
    /// Author email override.
    pub author_email: Option<String>,
    /// Allow empty commits.
    pub allow_empty: bool,
    /// Environment variables for the Git process.
    pub envs: serde_json::Map<String, Value>,
    /// Server-side timeout in seconds.
    pub timeout_seconds: Option<u64>,
}

/// Options for resetting Git state.
#[derive(Clone, Debug, Default)]
pub struct GitResetOptions {
    /// Reset mode: soft, mixed, hard, merge, or keep.
    pub mode: Option<String>,
    /// Target commit or ref.
    pub target: Option<String>,
    /// Optional pathspecs.
    pub paths: Vec<String>,
    /// Environment variables for the Git process.
    pub envs: serde_json::Map<String, Value>,
    /// Server-side timeout in seconds.
    pub timeout_seconds: Option<u64>,
}

/// Options for restoring Git paths.
#[derive(Clone, Debug, Default)]
pub struct GitRestoreOptions {
    /// Pathspecs to restore.
    pub paths: Vec<String>,
    /// Restore staged content.
    pub staged: Option<bool>,
    /// Restore worktree content.
    pub worktree: Option<bool>,
    /// Source tree-ish.
    pub source: Option<String>,
    /// Environment variables for the Git process.
    pub envs: serde_json::Map<String, Value>,
    /// Server-side timeout in seconds.
    pub timeout_seconds: Option<u64>,
}

/// Options for pulling or pushing.
#[derive(Clone, Debug, Default)]
pub struct GitRemoteOperationOptions {
    /// Remote name.
    pub remote: Option<String>,
    /// Branch name.
    pub branch: Option<String>,
    /// Set upstream during push.
    pub set_upstream: bool,
    /// Optional username for private HTTP(S) remotes.
    pub username: Option<String>,
    /// Optional password or token for private HTTP(S) remotes.
    pub password: Option<String>,
    /// Environment variables for the Git process.
    pub envs: serde_json::Map<String, Value>,
    /// Server-side timeout in seconds.
    pub timeout_seconds: Option<u64>,
}

/// Options for adding a Git remote.
#[derive(Clone, Debug, Default)]
pub struct GitRemoteAddOptions {
    /// Fetch after adding the remote.
    pub fetch: bool,
    /// Replace an existing remote with the same name.
    pub overwrite: bool,
    /// Environment variables for the Git process.
    pub envs: serde_json::Map<String, Value>,
    /// Server-side timeout in seconds.
    pub timeout_seconds: Option<u64>,
}

/// Options for Git config operations.
#[derive(Clone, Debug, Default)]
pub struct GitConfigOptions {
    /// Config scope: `global` or `local`.
    pub scope: Option<String>,
    /// Repository path for local scope.
    pub path: Option<String>,
    /// Environment variables for the Git process.
    pub envs: serde_json::Map<String, Value>,
    /// Server-side timeout in seconds.
    pub timeout_seconds: Option<u64>,
}

/// Git helper backed by sandbox data-plane routes.
#[derive(Clone)]
pub struct Git {
    data_plane: DataPlaneClient,
}

impl Git {
    pub(crate) fn new(data_plane: DataPlaneClient) -> Self {
        Self { data_plane }
    }

    /// Clone a Git repository into the sandbox.
    pub async fn clone(&self, url: &str, opts: GitCloneOptions) -> Result<GitCommandResult> {
        let mut body = serde_json::Map::new();
        body.insert("url".into(), Value::String(url.to_string()));
        put_if_some_string(&mut body, "path", opts.path);
        put_if_some_string(&mut body, "branch", opts.branch);
        put_if_some_u64(&mut body, "depth", opts.depth);
        if opts.recursive {
            body.insert("recursive".into(), Value::Bool(true));
        }
        if opts.submodules {
            body.insert("submodules".into(), Value::Bool(true));
        }
        put_if_some_string(&mut body, "username", opts.username);
        put_if_some_string(&mut body, "password", opts.password);
        if opts.dangerously_store_credentials {
            body.insert("dangerously_store_credentials".into(), Value::Bool(true));
        }
        put_request_options(&mut body, opts.envs, opts.timeout_seconds);
        self.run("/runtime/v1/git/clone", Value::Object(body)).await
    }

    /// Store Git credentials in the sandbox credential helper.
    pub async fn dangerously_authenticate(
        &self,
        opts: GitCredentialOptions,
    ) -> Result<GitCommandResult> {
        let mut body = serde_json::Map::new();
        body.insert("username".into(), Value::String(opts.username));
        body.insert("password".into(), Value::String(opts.password));
        put_if_some_string(&mut body, "host", opts.host);
        put_if_some_string(&mut body, "protocol", opts.protocol);
        put_request_options(&mut body, opts.envs, opts.timeout_seconds);
        self.run(
            "/runtime/v1/git/dangerously_authenticate",
            Value::Object(body),
        )
        .await
    }

    /// Configure Git author identity globally or for one repository.
    pub async fn configure_user(
        &self,
        name: &str,
        email: &str,
        opts: GitConfigureUserOptions,
    ) -> Result<GitCommandResult> {
        let mut body = serde_json::Map::new();
        body.insert("name".into(), Value::String(name.to_string()));
        body.insert("email".into(), Value::String(email.to_string()));
        put_if_some_string(&mut body, "scope", opts.scope);
        put_if_some_string(&mut body, "path", opts.path);
        put_request_options(&mut body, opts.envs, opts.timeout_seconds);
        self.run("/runtime/v1/git/configure_user", Value::Object(body))
            .await
    }

    /// Initialize a Git repository.
    pub async fn init(&self, path: &str, opts: GitInitOptions) -> Result<GitCommandResult> {
        let mut body = serde_json::Map::new();
        body.insert("path".into(), Value::String(path.to_string()));
        if opts.bare {
            body.insert("bare".into(), Value::Bool(true));
        }
        put_if_some_string(&mut body, "initial_branch", opts.initial_branch);
        put_request_options(&mut body, opts.envs, opts.timeout_seconds);
        self.run("/runtime/v1/git/init", Value::Object(body)).await
    }

    /// Return parsed repository status for `path`.
    pub async fn status(&self, path: &str, opts: GitRequestOptions) -> Result<GitStatus> {
        let result = self
            .run("/runtime/v1/git/status", repo_body(path, opts))
            .await?;
        Ok(parse_status(result))
    }

    /// Return branches and the current branch for `path`.
    pub async fn branches(&self, path: &str, opts: GitRequestOptions) -> Result<GitBranches> {
        let result = self
            .run("/runtime/v1/git/branches", repo_body(path, opts))
            .await?;
        Ok(GitBranches {
            path: result.path.clone(),
            branches: result.branches.clone(),
            current_branch: result.current_branch.clone(),
            result,
        })
    }

    /// Create and check out a new branch.
    pub async fn create_branch(
        &self,
        path: &str,
        branch: &str,
        opts: GitRequestOptions,
    ) -> Result<GitCommandResult> {
        let mut body = object_from(repo_body(path, opts));
        body.insert("branch".into(), Value::String(branch.to_string()));
        self.run("/runtime/v1/git/create_branch", Value::Object(body))
            .await
    }

    /// Delete a branch.
    pub async fn delete_branch(
        &self,
        path: &str,
        branch: &str,
        opts: GitDeleteBranchOptions,
    ) -> Result<GitCommandResult> {
        let mut body = serde_json::Map::new();
        body.insert("path".into(), Value::String(path.to_string()));
        body.insert("branch".into(), Value::String(branch.to_string()));
        if opts.force {
            body.insert("force".into(), Value::Bool(true));
        }
        put_request_options(&mut body, opts.envs, opts.timeout_seconds);
        self.run("/runtime/v1/git/delete_branch", Value::Object(body))
            .await
    }

    /// Stage files. Empty `files` means all files.
    pub async fn add(&self, path: &str, opts: GitAddOptions) -> Result<GitCommandResult> {
        let mut body = serde_json::Map::new();
        body.insert("path".into(), Value::String(path.to_string()));
        if !opts.files.is_empty() {
            body.insert(
                "files".into(),
                Value::Array(opts.files.into_iter().map(Value::String).collect()),
            );
        }
        put_request_options(&mut body, opts.envs, opts.timeout_seconds);
        self.run("/runtime/v1/git/add", Value::Object(body)).await
    }

    /// Commit staged files.
    pub async fn commit(
        &self,
        path: &str,
        message: &str,
        opts: GitCommitOptions,
    ) -> Result<GitCommandResult> {
        let mut body = serde_json::Map::new();
        body.insert("path".into(), Value::String(path.to_string()));
        body.insert("message".into(), Value::String(message.to_string()));
        put_if_some_string(&mut body, "author_name", opts.author_name);
        put_if_some_string(&mut body, "author_email", opts.author_email);
        if opts.allow_empty {
            body.insert("allow_empty".into(), Value::Bool(true));
        }
        put_request_options(&mut body, opts.envs, opts.timeout_seconds);
        self.run("/runtime/v1/git/commit", Value::Object(body))
            .await
    }

    /// Reset the current HEAD to a specified state.
    pub async fn reset(&self, path: &str, opts: GitResetOptions) -> Result<GitCommandResult> {
        let mut body = serde_json::Map::new();
        body.insert("path".into(), Value::String(path.to_string()));
        put_if_some_string(&mut body, "mode", opts.mode);
        put_if_some_string(&mut body, "target", opts.target);
        put_string_array(&mut body, "paths", opts.paths);
        put_request_options(&mut body, opts.envs, opts.timeout_seconds);
        self.run("/runtime/v1/git/reset", Value::Object(body)).await
    }

    /// Restore working tree files or unstage changes.
    pub async fn restore(&self, path: &str, opts: GitRestoreOptions) -> Result<GitCommandResult> {
        let mut body = serde_json::Map::new();
        body.insert("path".into(), Value::String(path.to_string()));
        put_string_array(&mut body, "paths", opts.paths);
        put_if_some_bool(&mut body, "staged", opts.staged);
        put_if_some_bool(&mut body, "worktree", opts.worktree);
        put_if_some_string(&mut body, "source", opts.source);
        put_request_options(&mut body, opts.envs, opts.timeout_seconds);
        self.run("/runtime/v1/git/restore", Value::Object(body))
            .await
    }

    /// Pull the current branch with a fast-forward-only merge.
    pub async fn pull(
        &self,
        path: &str,
        opts: GitRemoteOperationOptions,
    ) -> Result<GitCommandResult> {
        self.run(
            "/runtime/v1/git/pull",
            remote_operation_body(path, opts, false),
        )
        .await
    }

    /// Push the current branch or a selected branch.
    pub async fn push(
        &self,
        path: &str,
        opts: GitRemoteOperationOptions,
    ) -> Result<GitCommandResult> {
        self.run(
            "/runtime/v1/git/push",
            remote_operation_body(path, opts, true),
        )
        .await
    }

    /// Check out an arbitrary ref in a repository.
    pub async fn checkout(
        &self,
        path: &str,
        ref_name: &str,
        opts: GitRequestOptions,
    ) -> Result<GitCommandResult> {
        let mut body = object_from(repo_body(path, opts));
        body.insert("ref".into(), Value::String(ref_name.to_string()));
        self.run("/runtime/v1/git/checkout", Value::Object(body))
            .await
    }

    /// Check out an existing branch in a repository.
    pub async fn checkout_branch(
        &self,
        path: &str,
        branch: &str,
        opts: GitRequestOptions,
    ) -> Result<GitCommandResult> {
        self.checkout(path, branch, opts).await
    }

    /// Add a remote.
    pub async fn remote_add(
        &self,
        path: &str,
        name: &str,
        url: &str,
        opts: GitRemoteAddOptions,
    ) -> Result<GitCommandResult> {
        let mut body = serde_json::Map::new();
        body.insert("path".into(), Value::String(path.to_string()));
        body.insert("name".into(), Value::String(name.to_string()));
        body.insert("url".into(), Value::String(url.to_string()));
        if opts.fetch {
            body.insert("fetch".into(), Value::Bool(true));
        }
        if opts.overwrite {
            body.insert("overwrite".into(), Value::Bool(true));
        }
        put_request_options(&mut body, opts.envs, opts.timeout_seconds);
        self.run("/runtime/v1/git/remote_add", Value::Object(body))
            .await
    }

    /// Return a remote URL, or `None` when the remote does not exist.
    pub async fn remote_get(
        &self,
        path: &str,
        name: &str,
        opts: GitRequestOptions,
    ) -> Result<Option<String>> {
        let mut body = object_from(repo_body(path, opts));
        body.insert("name".into(), Value::String(name.to_string()));
        let result = self
            .run("/runtime/v1/git/remote_get", Value::Object(body))
            .await?;
        Ok(result.value.or(result.url))
    }

    /// Set a Git config value.
    pub async fn set_config(
        &self,
        key: &str,
        value: &str,
        opts: GitConfigOptions,
    ) -> Result<GitCommandResult> {
        let mut body = config_body(key, opts);
        body.insert("value".into(), Value::String(value.to_string()));
        self.run("/runtime/v1/git/set_config", Value::Object(body))
            .await
    }

    /// Read a Git config value.
    pub async fn get_config(&self, key: &str, opts: GitConfigOptions) -> Result<String> {
        let body = config_body(key, opts);
        let result = self
            .run("/runtime/v1/git/get_config", Value::Object(body))
            .await?;
        Ok(result.value.unwrap_or_default())
    }

    async fn run(&self, path: &str, body: Value) -> Result<GitCommandResult> {
        let payload = self.data_plane.post_json(path, body).await?;
        Ok(git_result(payload.get("git").unwrap_or(&payload)))
    }
}

fn repo_body(path: &str, opts: GitRequestOptions) -> Value {
    let mut body = serde_json::Map::new();
    body.insert("path".into(), Value::String(path.to_string()));
    put_request_options(&mut body, opts.envs, opts.timeout_seconds);
    Value::Object(body)
}

fn remote_operation_body(path: &str, opts: GitRemoteOperationOptions, push: bool) -> Value {
    let mut body = serde_json::Map::new();
    body.insert("path".into(), Value::String(path.to_string()));
    put_if_some_string(&mut body, "remote", opts.remote);
    put_if_some_string(&mut body, "branch", opts.branch);
    put_if_some_string(&mut body, "username", opts.username);
    put_if_some_string(&mut body, "password", opts.password);
    if push && opts.set_upstream {
        body.insert("set_upstream".into(), Value::Bool(true));
    }
    put_request_options(&mut body, opts.envs, opts.timeout_seconds);
    Value::Object(body)
}

fn config_body(key: &str, opts: GitConfigOptions) -> serde_json::Map<String, Value> {
    let mut body = serde_json::Map::new();
    body.insert("key".into(), Value::String(key.to_string()));
    put_if_some_string(&mut body, "scope", opts.scope);
    put_if_some_string(&mut body, "path", opts.path);
    put_request_options(&mut body, opts.envs, opts.timeout_seconds);
    body
}

fn put_request_options(
    body: &mut serde_json::Map<String, Value>,
    envs: serde_json::Map<String, Value>,
    timeout_seconds: Option<u64>,
) {
    if !envs.is_empty() {
        body.insert("env_vars".into(), Value::Object(envs));
    }
    put_if_some_u64(body, "timeout_seconds", timeout_seconds);
}

fn git_result(value: &Value) -> GitCommandResult {
    GitCommandResult {
        path: string_value(value, "path"),
        url: string_value(value, "url"),
        ref_name: string_value(value, "ref"),
        branch: string_value(value, "branch"),
        remote: string_value(value, "remote"),
        name: string_value(value, "name"),
        value: string_value(value, "value"),
        branches: value
            .get("branches")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(Value::as_str)
                    .map(ToOwned::to_owned)
                    .collect()
            })
            .unwrap_or_default(),
        current_branch: string_value(value, "current_branch"),
        stdout: string_value(value, "stdout").unwrap_or_default(),
        stderr: string_value(value, "stderr").unwrap_or_default(),
        command: value.get("command").cloned(),
        raw: value.clone(),
    }
}

fn parse_status(result: GitCommandResult) -> GitStatus {
    let mut status = GitStatus {
        result,
        ..GitStatus::default()
    };

    for line in status.result.stdout.lines().filter(|line| !line.is_empty()) {
        if let Some(branch_line) = line.strip_prefix("## ") {
            status.detached = branch_line.contains("HEAD") && branch_line.contains("no branch");
            if let Some((branch, tracking)) = branch_line.split_once("...") {
                status.current_branch =
                    Some(branch.split(" [").next().unwrap_or(branch).to_string());
                status.upstream = tracking
                    .split([' ', '['])
                    .next()
                    .filter(|value| !value.is_empty())
                    .map(ToOwned::to_owned);
                status.ahead = number_after(tracking, "ahead");
                status.behind = number_after(tracking, "behind");
            } else {
                status.current_branch = Some(
                    branch_line
                        .split(" [")
                        .next()
                        .unwrap_or(branch_line)
                        .to_string(),
                );
            }
            continue;
        }

        let index_status = line.chars().next().unwrap_or(' ');
        let working_tree_status = line.chars().nth(1).unwrap_or(' ');
        let name = line.get(3..).unwrap_or_default();
        let (renamed_from, name) = name
            .split_once(" -> ")
            .map(|(from, to)| (Some(from.to_string()), to.to_string()))
            .unwrap_or((None, name.to_string()));
        status.file_status.push(GitFileStatus {
            name,
            status: status_name(index_status, working_tree_status).to_string(),
            index_status: index_status.to_string(),
            working_tree_status: working_tree_status.to_string(),
            staged: index_status != ' ' && index_status != '?',
            renamed_from,
        });
    }

    status
}

fn number_after(value: &str, label: &str) -> u64 {
    value
        .split_once(&format!("{label} "))
        .and_then(|(_, rest)| rest.split([',', ']']).next())
        .and_then(|number| number.parse().ok())
        .unwrap_or(0)
}

fn status_name(index_status: char, working_tree_status: char) -> &'static str {
    match (index_status, working_tree_status) {
        ('?', '?') => "untracked",
        ('U', _) | (_, 'U') | ('A', 'A') => "conflict",
        ('D', _) | (_, 'D') => "deleted",
        ('R', _) => "renamed",
        ('A', _) => "added",
        ('M', _) | (_, 'M') => "modified",
        _ => "changed",
    }
}

fn string_value(value: &Value, key: &str) -> Option<String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .map(ToOwned::to_owned)
}

fn put_if_some_string(map: &mut serde_json::Map<String, Value>, key: &str, value: Option<String>) {
    if let Some(value) = value {
        map.insert(key.to_string(), Value::String(value));
    }
}

fn put_if_some_u64(map: &mut serde_json::Map<String, Value>, key: &str, value: Option<u64>) {
    if let Some(value) = value {
        map.insert(key.to_string(), Value::from(value));
    }
}

fn put_if_some_bool(map: &mut serde_json::Map<String, Value>, key: &str, value: Option<bool>) {
    if let Some(value) = value {
        map.insert(key.to_string(), Value::Bool(value));
    }
}

fn put_string_array(map: &mut serde_json::Map<String, Value>, key: &str, values: Vec<String>) {
    if !values.is_empty() {
        map.insert(
            key.to_string(),
            Value::Array(values.into_iter().map(Value::String).collect()),
        );
    }
}

fn object_from(value: Value) -> serde_json::Map<String, Value> {
    value.as_object().cloned().unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::{git_result, parse_status};
    use serde_json::json;

    #[test]
    fn parses_short_branch_status() {
        let result = git_result(&json!({
            "path": "/workspace/repo",
            "stdout": "## main...origin/main [ahead 1, behind 2]\n M a.txt\n?? b.txt\n",
            "stderr": ""
        }));

        let status = parse_status(result);

        assert_eq!(status.current_branch.as_deref(), Some("main"));
        assert_eq!(status.upstream.as_deref(), Some("origin/main"));
        assert_eq!(status.ahead, 1);
        assert_eq!(status.behind, 2);
        assert!(status.has_changes());
        assert!(status.has_untracked());
        assert_eq!(status.total_count(), 2);
    }
}
