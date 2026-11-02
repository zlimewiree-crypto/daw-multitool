"""DAW Multitool entry point, including frozen multiprocessing workers."""
if __name__ == "__main__":
    from multiprocessing import freeze_support

    freeze_support()  # divert frozen audio workers before loading the application
    from multitool.app import main

    raise SystemExit(main())
