"""Standalone HTTP service for the LabSafe agent."""

from flask import Flask, abort, jsonify, request, send_file

from .orchestrator import AgentOrchestrator


def create_app():
    app = Flask(__name__)
    orchestrator = AgentOrchestrator()

    @app.route("/health")
    def health():
        return jsonify({"success": True, "service": "labsafe-agent"})

    @app.route("/api/agent/status")
    def agent_status():
        return jsonify(orchestrator.status())

    @app.route("/api/agent/chat", methods=["POST"])
    def agent_chat():
        data = request.get_json(silent=True) or {}
        return jsonify(orchestrator.chat(
            data.get("message", ""),
            data.get("sender", "user"),
            deep_thinking=data.get("deep_thinking", False),
            web_search=data.get("web_search", False),
            attachment_ids=data.get("attachment_ids") or [],
            session_id=data.get("session_id") or data.get("conversation_id"),
        ))

    @app.route("/api/agent/uploads", methods=["POST"])
    def agent_uploads():
        file_storage = request.files.get("file")
        return jsonify(orchestrator.upload_file(file_storage))

    @app.route("/api/agent/uploads/<file_id>/content")
    def agent_upload_content(file_id):
        path = orchestrator.upload_file_path(file_id, thumbnail=False)
        if not path:
            abort(404)
        return send_file(path, max_age=0)

    @app.route("/api/agent/uploads/<file_id>/thumbnail")
    def agent_upload_thumbnail(file_id):
        path = orchestrator.upload_file_path(file_id, thumbnail=True)
        if not path:
            abort(404)
        return send_file(path, mimetype="image/jpeg", max_age=0)

    @app.route("/api/agent/action/confirm", methods=["POST"])
    def agent_confirm():
        data = request.get_json(silent=True) or {}
        return jsonify(orchestrator.confirm_action(data.get("token", "")))

    @app.route("/api/agent/enable", methods=["POST"])
    def agent_enable():
        return jsonify(orchestrator.set_enabled(True))

    @app.route("/api/agent/disable", methods=["POST"])
    def agent_disable():
        return jsonify(orchestrator.set_enabled(False))

    @app.route("/api/agent/models")
    def agent_models():
        return jsonify(orchestrator.models())

    @app.route("/api/agent/models/select", methods=["POST"])
    def agent_select_model():
        data = request.get_json(silent=True) or {}
        return jsonify(orchestrator.select_model(data.get("provider", ""), data.get("model", "")))

    @app.route("/api/agent/models/test", methods=["POST"])
    def agent_test_model():
        data = request.get_json(silent=True) or {}
        return jsonify(orchestrator.test_model(data.get("provider", ""), data.get("model", "")))

    return app


if __name__ == "__main__":
    from .config import AGENT_HOST, AGENT_PORT

    create_app().run(host=AGENT_HOST, port=AGENT_PORT, debug=False, threaded=True)
