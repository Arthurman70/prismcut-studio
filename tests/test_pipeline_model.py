from prismcut.core.pipeline import MoviePipeline, Scene, SceneHistory, StageAsset, new_scene


def test_scene_history_push_and_active():
    h = SceneHistory()
    assert h.active is None
    h.push(StageAsset(media_id="a1", prompt="first take"))
    assert h.active.media_id == "a1"
    h.push(StageAsset(media_id="a2", prompt="second take"))
    assert h.active.media_id == "a2"
    assert len(h.entries) == 2


def test_scene_history_undo_then_new_take_truncates_redo_forward():
    h = SceneHistory()
    h.push(StageAsset(media_id="a1"))
    h.push(StageAsset(media_id="a2"))
    h.current = 0   # simulate "jump back to take 1" (same idiom as Photo Studio's filmstrip click)
    assert h.active.media_id == "a1"
    h.push(StageAsset(media_id="a3"))   # a new take from here drops the abandoned "a2" branch
    assert [e.media_id for e in h.entries] == ["a1", "a3"]
    assert h.active.media_id == "a3"


def test_scene_round_trips_through_dict():
    s = new_scene(0)
    s.script = "Opening shot"
    s.image.push(StageAsset(media_id="img1"))
    d = {"id": s.id, "index": s.index, "script": s.script, "narration": s.narration,
        "image": {"entries": [e.__dict__ for e in s.image.entries], "current": s.image.current},
        "audio": {"entries": [], "current": -1}, "video": {"entries": [], "current": -1},
        "lipsync": {"entries": [], "current": -1}, "use_lipsync": False, "clip_ids": {}}
    restored = Scene.from_dict(d)
    assert restored.script == "Opening shot"
    assert restored.image.active.media_id == "img1"


def test_moviepipeline_save_and_load_round_trip(tmp_path):
    p = MoviePipeline(name="Test movie", brief="A robot learns to paint.")
    scene = new_scene(0)
    scene.script = "The robot picks up a brush."
    scene.image.push(StageAsset(media_id="img1", prompt="a robot holding a paintbrush"))
    p.scenes.append(scene)

    out = tmp_path / "pipeline.json"
    p.save(out)
    loaded = MoviePipeline.load(out)

    assert loaded.name == "Test movie"
    assert loaded.brief == p.brief
    assert len(loaded.scenes) == 1
    assert loaded.scenes[0].script == "The robot picks up a brush."
    assert loaded.scenes[0].image.active.media_id == "img1"
    assert loaded.scene(scene.id) is not None
    assert loaded.scene("nonexistent") is None


def test_moviepipeline_default_status_is_draft():
    p = MoviePipeline()
    assert p.status == "draft"
    assert p.id  # auto-generated, never blank


def test_scene_image_video_model_default_to_empty_string():
    s = new_scene(0)
    assert s.image_model == ""
    assert s.video_model == ""


def test_scene_model_overrides_round_trip_through_save_and_load(tmp_path):
    p = MoviePipeline(name="Test movie", image_model="google::gemini-3-pro-image",
                      video_model="google::veo-3.1-generate-preview")
    scene = new_scene(0)
    scene.image_model = "openai::gpt-image-2"
    scene.video_model = "xai::grok-imagine-video-1.5"
    p.scenes.append(scene)

    out = tmp_path / "pipeline.json"
    p.save(out)
    loaded = MoviePipeline.load(out)

    assert loaded.scenes[0].image_model == "openai::gpt-image-2"
    assert loaded.scenes[0].video_model == "xai::grok-imagine-video-1.5"
