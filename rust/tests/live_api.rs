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

    assert!(
        Template::exists("base", ConnectionOptions::default())
            .await
            .expect("live template alias request should succeed")
    );
    assert!(
        !Template::exists(
            "watasu-live-missing-template",
            ConnectionOptions::default()
        )
        .await
        .expect("live missing template alias request should succeed")
    );
    let _tags = Template::get_tags("base", ConnectionOptions::default())
        .await
        .expect("live template tags request should succeed");
}
