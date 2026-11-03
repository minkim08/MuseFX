"""Runpod Serverless worker for AudioGen.

Takes the same input and returns the same output as model.py's predict(). The
model loads before the worker takes jobs, so however long a cold start takes,
jobs simply wait in Runpod's queue.
"""


def make_handler(model):
    def handler(event):
        try:
            return model.predict(event["input"])
        except (KeyError, TypeError, ValueError) as e:
            return {"error": f"bad request: {e!r}"}

    return handler


if __name__ == "__main__":
    import runpod
    from model import Model

    model = Model()
    model.load()
    runpod.serverless.start({"handler": make_handler(model)})
