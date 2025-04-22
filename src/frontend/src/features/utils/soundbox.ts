class Soundbox {
    #soundCache = new Map<string, HTMLAudioElement>();
    audio: HTMLAudioElement | undefined = undefined;

    async load(src: string) {
        if (this.#soundCache.has(src)) {
            this.audio = this.#soundCache.get(src);
        } else {
            const audio = new Audio(src);
            this.#soundCache.set(src, audio);
            this.audio = audio;
        }
        this.audio!.currentTime = 0;
    }

    async play(volume: number = 1) {
        this.audio!.volume = volume;
        await this.audio!.play();
    }
}

const soundbox = new Soundbox();
export default soundbox;