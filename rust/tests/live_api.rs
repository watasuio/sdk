use watasu::{ConnectionOptions, Sandbox, SnapshotListOptions, Template};

#[tokio::test]
async fn live_snapshot_list_shape() {
    if std::env::var("WATASU_LIVE_API_TESTS").ok().as_deref() != Some("1") {
        eprintln!("set WATASU_LIVE_API_TESTS=1 to run live API smoke tests");
        return;
    }

    let page = Sandbox::list_snapshots_page(SnapshotListOptions {
        limit: Some(2),
        ..SnapshotListOptions::default()
    })
    .await
    .expect("live snapshot list request should succeed");

    assert!(page.snapshots.len() <= 2);
}

#[tokio::test]
async fn live_template_helpers_expose_platform_template_aliases() {
    if std::env::var("WATASU_LIVE_API_TESTS").ok().as_deref() != Some("1") {
        eprintln!("set WATASU_LIVE_API_TESTS=1 to run live API smoke tests");
        return;
    }

    assert!(Template::exists("base", ConnectionOptions::default())
        .await
        .expect("live template alias request should succeed"));
    assert!(
        !Template::exists("watasu-live-missing-template", ConnectionOptions::default())
            .await
            .expect("live missing template alias request should succeed")
    );
    let _tags = Template::get_tags("base", ConnectionOptions::default())
        .await
        .expect("live template tags request should succeed");
}

#[tokio::test]
async fn live_sandbox_template_admin_reads_version_and_logs() {
    if std::env::var("WATASU_LIVE_API_TESTS").ok().as_deref() != Some("1") {
        eprintln!("set WATASU_LIVE_API_TESTS=1 to run live API smoke tests");
        return;
    }

    let templates = Template::list_sandbox_templates(ConnectionOptions::default())
        .await
        .expect("live sandbox template list request should succeed");
    let base = templates
        .iter()
        .find(|template| template.slug == "base")
        .expect("live platform base template should be listed");

    let found = Template::find_sandbox_template_by_slug("base", ConnectionOptions::default())
        .await
        .expect("live sandbox template slug lookup should succeed")
        .expect("live platform base template should be found");
    assert_eq!(found.template_id, base.template_id);

    let versions =
        Template::list_sandbox_template_versions(&base.template_id, ConnectionOptions::default())
            .await
            .expect("live sandbox template versions request should succeed");
    let version = versions
        .first()
        .expect("live platform base template should have at least one version");
    assert!(!version.template_version_id.is_empty());
    assert!(!version.status.is_empty());

    let fetched = Template::get_sandbox_template_version(
        &base.template_id,
        &version.template_version_id,
        ConnectionOptions::default(),
    )
    .await
    .expect("live sandbox template version request should succeed");
    assert_eq!(fetched.template_version_id, version.template_version_id);

    let logs = Template::get_sandbox_template_version_build_logs(
        &base.template_id,
        &version.template_version_id,
        ConnectionOptions::default(),
    )
    .await
    .expect("live sandbox template build logs request should succeed");
    assert_eq!(logs.template_version_id, version.template_version_id);
    assert!(!logs.status.is_empty());
}
